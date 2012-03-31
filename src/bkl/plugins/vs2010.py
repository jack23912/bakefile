#
#  This file is part of Bakefile (http://www.bakefile.org)
#
#  Copyright (C) 2011-2012 Vaclav Slavik
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to
#  deal in the Software without restriction, including without limitation the
#  rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
#  sell copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be included in
#  all copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
#  FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
#  IN THE SOFTWARE.
#

import types
import codecs
from xml.sax.saxutils import escape, quoteattr

import bkl.compilers
import bkl.expr
from bkl.error import error_context, warning, Error
from bkl.api import Toolset, Property
from bkl.vartypes import PathType, StringType, BoolType
from bkl.utils import OrderedDict
from bkl.io import OutputFile, EOL_WINDOWS

from bkl.plugins.vsbase import *


class Node(object):
    def __init__(self, name, text=None, **kwargs):
        self.name = name
        self.text = text
        self.attrs = OrderedDict()
        self.children = []
        self.attrs.update(kwargs)

    def __setitem__(self, key, value):
        self.attrs[key] = value

    def __getitem__(self, key):
        return self.attrs[key]

    def add(self, *args, **kwargs):
        """
        Add a child to this node. There are several ways of invoking add():

        The argument may be another node:
        >>> n.add(Node("foo"))

        Or it may be key-value pair, where the value is bkl.expr.Expr or any Python
        value convertible to string:
        >>> n.add("ProjectGuid", "{31DC1570-67C5-40FD-9130-C5F57BAEBA88}")
        >>> n.add("LinkIncremental", target["vs-incremental-link"])

        Or it can take the same arguments that Node constructor takes:
        >>> n.add("ImportGroup", Label="PropertySheets")
        """
        assert len(args) > 0
        arg0 = args[0]
        if len(args) == 1:
            if isinstance(arg0, Node):
                self.children.append((arg0.name, arg0))
                return
            elif isinstance(arg0, types.StringType):
                self.children.append((arg0, Node(arg0, **kwargs)))
                return
        elif len(args) == 2:
            if isinstance(arg0, types.StringType) and len(kwargs) == 0:
                    self.children.append((arg0, args[1]))
                    return
        assert 0, "add() is confused: what are you trying to do?"

    def has_children(self):
        return len(self.children) > 0


class VS2010ExprFormatter(bkl.expr.Formatter):
    list_sep = ";"

    def reference(self, e):
        assert False, "All references should be expanded in VS output"

    def bool_value(self, e):
        return "true" if e.value else "false"


XML_HEADER = """\
<?xml version="1.0" encoding="utf-8"?>
<!-- This file was generated by Bakefile (http://bakefile.org). Do not modify, all changes will be overwritten! -->
"""

class XmlFormatter(object):
    """
    Formats Node hierarchy into XML output that looks like Visual Studio's native format.
    """

    def __init__(self, paths_info):
        self.expr_formatter = VS2010ExprFormatter(paths_info)

    def format(self, node):
        return XML_HEADER + self._format_node(node, "")

    def _format_node(self, n, indent):
        s = "%s<%s" % (indent, n.name)
        for key, value in n.attrs.iteritems():
            s += ' %s=%s' % (key, quoteattr(self._format_value(value)))
        if n.children:
            assert not n.text, "nodes with both text and children not implemented"
            s += ">\n"
            subindent = indent + "  "
            for key, value in n.children:
                if isinstance(value, Node):
                    assert key == value.name
                    s += self._format_node(value, subindent)
                else:
                    v = escape(self._format_value(value))
                    if v:
                        s += "%s<%s>%s</%s>\n" % (subindent, key, v, key)
                    # else: empty value, don't write that

            s += "%s</%s>\n" % (indent, n.name)
        elif n.text:
            s += ">%s</%s>\n" % (n.text, n.name)
        else:
            s += " />\n"
        return s

    def _format_value(self, val):
        if isinstance(val, bkl.expr.Expr):
            s = self.expr_formatter.format(val)
        elif isinstance(val, types.BooleanType):
            s = "true" if val else "false"
        elif isinstance(val, types.ListType):
            s = ";".join(self._format_value(x) for x in val)
        else:
            s = str(val)
        return s


class VS2010Solution(OutputFile):

    format_version = "11.00"
    human_version = "2010"

    def __init__(self, toolset, module):
        slnfile = module["%s.solutionfile" % toolset.name].as_native_path_for_output(module)
        super(VS2010Solution, self).__init__(slnfile, EOL_WINDOWS,
                                             creator=toolset, create_for=module)
        self.name = module.name
        self.guid = GUID(NAMESPACE_SLN_GROUP, module.project.top_module.name, module.name)
        self.write_header()
        self.projects = []
        self.subsolutions = []
        self.parent_solution = None
        self.guids_map = {}
        paths_info = bkl.expr.PathAnchorsInfo(
                                    dirsep="\\",
                                    outfile=slnfile,
                                    builddir=None,
                                    model=module)
        self.formatter = VS2010ExprFormatter(paths_info)

    def write_header(self):
        self.write(codecs.BOM_UTF8)
        self.write("\n")
        self.write("Microsoft Visual Studio Solution File, Format Version %s\n" % self.format_version)
        self.write("# Visual Studio %s\n" % self.human_version)

    def add_project(self, prj):
        self.guids_map[prj.name] = prj.guid
        # TODO: store VSProjectBase instances directly here
        self.projects.append((prj.name, prj.guid, prj.projectfile, prj.dependencies))

    def add_subsolution(self, solution):
        self.subsolutions.append(solution)
        solution.parent_solution = self

    def all_projects(self):
        for p in self.projects:
            yield p
        for sln in self.subsolutions:
            for p in sln.all_projects():
                yield p

    def all_subsolutions(self):
        for sln in self.subsolutions:
            yield sln
            for s in sln.all_subsolutions():
                yield s

    def _get_project_info(self, id):
        for p in self.projects:
            if p[0] == id:
                return p
        for sln in self.subsolutions:
            p = sln._get_project_info(id)
            if p:
                return p
        return None

    def additional_deps(self):
        """
        Returns additional projects to include, "external" deps e.g. from
        parents, in the same format all_projects() uses.
        """
        additional = []
        top = self
        while top.parent_solution:
            top = top.parent_solution
        if top is self:
            return additional

        included = set(x[0] for x in self.all_projects())
        todo = set()
        for name, guid, projectfile, deps in self.all_projects():
            todo.update(deps)

        prev_count = 0
        while prev_count != len(included):
            prev_count = len(included)
            todo = set(x for x in todo if x not in included)
            todo_new = set()
            for todo_item in todo:
                included.add(todo_item)
                prj = top._get_project_info(todo_item)
                todo_new.update(prj[3])
                additional.append(prj)
            todo.update(todo_new)
        return additional

    def _find_target_guid_recursively(self, id):
        """Recursively search for the target in all submodules and return its GUID."""
        if id in self.guids_map:
            return self.guids_map[id]
        for sln in self.subsolutions:
            guid = sln._find_target_guid_recursively(id)
            if guid:
                return guid
        return None

    def _get_target_guid(self, id):
        if id in self.guids_map:
            return self.guids_map[id]
        else:
            top = self
            while top.parent_solution:
                top = top.parent_solution
            guid = top._find_target_guid_recursively(id)
            assert guid, "can't find GUID of project '%s'" % id
            return guid

    def commit(self):
        # Projects:
        guids = []
        additional_deps = self.additional_deps()
        for name, guid, filename, deps in list(self.all_projects()) + additional_deps:
            guids.append(guid)
            self.write('Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "%s", "%s", "%s"\n' %
                       (name, self.formatter.format(filename), str(guid)))
            if deps:
                self.write("\tProjectSection(ProjectDependencies) = postProject\n")
                for d in deps:
                    self.write("\t\t%(g)s = %(g)s\n" % {'g':self._get_target_guid(d)})
                self.write("\tEndProjectSection\n")
            self.write("EndProject\n")

        if not guids:
            return # don't write empty solution files

        # Folders in the solution:
        all_folders = list(self.all_subsolutions())
        if additional_deps:
            class AdditionalDepsFolder: pass
            extras = AdditionalDepsFolder()
            extras.name = "Additional Dependencies"
            extras.guid = GUID(NAMESPACE_INTERNAL, self.name, extras.name)
            extras.projects = additional_deps
            extras.subsolutions = []
            extras.parent_solution = None
            all_folders.append(extras)
        for sln in all_folders:
            # don't have folders with just one item in them:
            sln.omit_from_tree = (sln.parent_solution and
                                  (len(sln.projects) + len(sln.subsolutions)) <= 1)
            if sln.omit_from_tree:
                continue
            self.write('Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "%s", "%s", "%s"\n' %
                       (sln.name, sln.name, sln.guid))
            self.write("EndProject\n")

        # Global settings:
        self.write("Global\n")
        self.write("\tGlobalSection(SolutionConfigurationPlatforms) = preSolution\n")
        self.write("\t\tDebug|Win32 = Debug|Win32\n")
        self.write("\t\tRelease|Win32 = Release|Win32\n")
        self.write("\tEndGlobalSection\n")
        self.write("\tGlobalSection(ProjectConfigurationPlatforms) = postSolution\n")
        for guid in guids:
            self.write("\t\t%s.Debug|Win32.ActiveCfg = Debug|Win32\n" % guid)
            self.write("\t\t%s.Debug|Win32.Build.0 = Debug|Win32\n" % guid)
            self.write("\t\t%s.Release|Win32.ActiveCfg = Release|Win32\n" % guid)
            self.write("\t\t%s.Release|Win32.Build.0 = Release|Win32\n" % guid)
        self.write("\tEndGlobalSection\n")
        self.write("\tGlobalSection(SolutionProperties) = preSolution\n")
        self.write("\t\tHideSolutionNode = FALSE\n")
        self.write("\tEndGlobalSection\n")

        # Nesting of projects and folders in the tree:
        if all_folders:
            self.write("\tGlobalSection(NestedProjects) = preSolution\n")
            for sln in all_folders:
                for prj in sln.projects:
                    prjguid = prj[1]
                    parentguid = sln.guid if not sln.omit_from_tree else sln.parent_solution.guid
                    self.write("\t\t%s = %s\n" % (prjguid, parentguid))
                for subsln in sln.subsolutions:
                    self.write("\t\t%s = %s\n" % (subsln.guid, sln.guid))
            self.write("\tEndGlobalSection\n")

        self.write("EndGlobal\n")
        super(VS2010Solution, self).commit()


# TODO: Put more content into this class, use it properly
class VS2010Project(VSProjectBase):
    """
    """
    version = 10

    def __init__(self, name, guid, projectfile, deps):
        self.name = name
        self.guid = guid
        self.projectfile = projectfile
        self.dependencies = deps



# TODO: Both of these should be done as an expression once proper functions
#       are implemented, as $(dirname(vs2010.solutionfile)/$(id).vcxproj)
def _default_solution_name(module):
    """same directory and name as the module's bakefile, with ``.sln`` extension"""
    return bkl.expr.PathExpr([bkl.expr.LiteralExpr(module.name + ".sln")])

def _project_name_from_solution(target):
    """``$(id).vcxproj`` in the same directory as the ``.sln`` file"""
    sln = target["vs2010.solutionfile"]
    return bkl.expr.PathExpr(sln.components[:-1] + [bkl.expr.LiteralExpr("%s.vcxproj" % target.name)], sln.anchor)

def _default_guid_for_project(target):
    """automatically generated"""
    return '"%s"' % GUID(NAMESPACE_PROJECT, target.parent.name, target.name)


class VS201xToolsetBase(Toolset):
    """Base class for VS2010 and VS11 toolsets."""

    # Project files versions that are natively supported, sorted list
    proj_versions = None
    # PlatformToolset property
    platform_toolset = None
    # Solution class for this VS version
    Solution = VS2010Solution
    #: Project class for this VS version
    Project = VS2010Solution

    exe_extension = "exe"
    library_extension = "lib"

    @classmethod
    def properties_target(cls):
        yield Property("%s.projectfile" % cls.name,
                       type=PathType(),
                       default=_project_name_from_solution,
                       inheritable=False,
                       doc="File name of the project for the target.")
        yield Property("%s.guid" % cls.name,
                       # TODO: use custom GUID type, so that user-provided GUIDs can be validated
                       # TODO: make this vs.guid and share among VS toolsets
                       type=StringType(),
                       default=_default_guid_for_project,
                       inheritable=False,
                       doc="GUID of the project.")

    @classmethod
    def properties_module(cls):
        yield Property("%s.solutionfile" % cls.name,
                       type=PathType(),
                       default=_default_solution_name,
                       inheritable=False,
                       doc="File name of the solution file for the module.")
        yield Property("%s.generate-solution" % cls.name,
                       type=BoolType(),
                       default=True,
                       inheritable=True,
                       doc="""
                           Whether to generate solution file for the module. Set to
                           ``false`` if you want to omit the solution, e.g. for some
                           submodules with only a single target.
                           """)

    def get_builddir_for(self, target):
        prj = target["%s.projectfile" % self.name]
        # TODO: reference Configuration setting properly, as bkl setting
        return bkl.expr.PathExpr(prj.components[:-1] + [bkl.expr.LiteralExpr("$(Configuration)")], prj.anchor)

    def generate(self, project):
        # generate vcxproj files and prepare solutions
        for m in project.modules:
            with error_context(m):
                self.gen_for_module(m)
        # Commit solutions; this must be done after processing all modules
        # because of inter-module dependencies and references.
        for m in project.modules:
            for sub in m.submodules:
                m.solution.add_subsolution(sub.solution)
        for m in project.modules:
            if m["%s.generate-solution" % self.name]:
                m.solution.commit()


    def gen_for_module(self, module):
        # attach VS2010-specific data to the model
        module.solution = self.Solution(self, module)

        for t in module.targets.itervalues():
            with error_context(t):
                prj = self.gen_for_target(t)
                if not prj:
                    # Not natively supported; try if the TargetType has an implementation
                    try:
                        prj = t.type.vs_project(self, t)
                    except NotImplementedError:
                        # TODO: handle this as generic action target
                        warning("target type \"%s\" is not supported by vs2010 toolset, ignoring", t.type.name)
                        continue
                if prj.name != t.name:
                    # TODO: This is only for the solution file; we should remap the name instead of
                    #       failure. Note that we don't always control prj.name, it may come from external
                    #       project file.
                    raise Error("project name (\"%s\") differs from target name (\"%s\"), they must be the same" %
                                (prj.name, t.name))
                if prj.version not in self.proj_versions:
                    if prj.version > self.proj_versions[-1]:
                        raise Error("project %s is for Visual Studio %s and will not work with %s" %
                                    (prj.projectfile, prj.version, self.version))
                    else:
                        warning("project %s is for Visual Studio %s, not %s, will be converted when built",
                                prj.projectfile, prj.version, self.version)
                module.solution.add_project(prj)


    def gen_for_target(self, target):
        projectfile = target["%s.projectfile" % self.name]
        filename = projectfile.as_native_path_for_output(target)

        paths_info = bkl.expr.PathAnchorsInfo(
                                    dirsep="\\",
                                    outfile=filename,
                                    builddir=self.get_builddir_for(target).as_native_path_for_output(target),
                                    model=target)

        is_library = (target.type.name == "library")
        is_exe = (target.type.name == "exe")
        is_dll = (target.type.name == "dll")

        root = Node("Project")
        root["DefaultTargets"] = "Build"
        root["ToolsVersion"] = "4.0"
        root["xmlns"] = "http://schemas.microsoft.com/developer/msbuild/2003"

        guid = target["%s.guid" % self.name]
        configs = ["Debug", "Release"]

        n_configs = Node("ItemGroup", Label="ProjectConfigurations")
        for c in configs:
            n = Node("ProjectConfiguration", Include="%s|Win32" % c)
            n.add("Configuration", c)
            n.add("Platform", "Win32")
            n_configs.add(n)
        root.add(n_configs)

        self._set_VCTargetsPath(root)

        n_globals = Node("PropertyGroup", Label="Globals")
        self._add_extra_options_to_node(target, n_globals)
        n_globals.add("ProjectGuid", guid)
        n_globals.add("Keyword", "Win32Proj")
        n_globals.add("RootNamespace", target.name)
        root.add(n_globals)

        root.add("Import", Project="$(VCTargetsPath)\\Microsoft.Cpp.Default.props")

        for c in configs:
            n = Node("PropertyGroup", Label="Configuration")
            self._add_extra_options_to_node(target, n)
            n["Condition"] = "'$(Configuration)|$(Platform)'=='%s|Win32'" % c
            if is_exe:
                n.add("ConfigurationType", "Application")
            elif is_library:
                n.add("ConfigurationType", "StaticLibrary")
            elif is_dll:
                n.add("ConfigurationType", "DynamicLibrary")
            else:
                return None

            n.add("UseDebugLibraries", c == "Debug")
            if self.platform_toolset:
                n.add("PlatformToolset", self.platform_toolset)
            if target["win32-unicode"]:
                n.add("CharacterSet", "Unicode")
            else:
                n.add("CharacterSet", "MultiByte")
            root.add(n)

        root.add("Import", Project="$(VCTargetsPath)\\Microsoft.Cpp.props")
        root.add("ImportGroup", Label="ExtensionSettings")

        for c in configs:
            n = Node("ImportGroup", Label="PropertySheets")
            n["Condition"] = "'$(Configuration)|$(Platform)'=='%s|Win32'" % c
            n.add("Import",
                  Project="$(UserRootDir)\\Microsoft.Cpp.$(Platform).user.props",
                  Condition="exists('$(UserRootDir)\\Microsoft.Cpp.$(Platform).user.props')",
                  Label="LocalAppDataPlatform")
            root.add(n)

        root.add("PropertyGroup", Label="UserMacros")

        for c in configs:
            n = Node("PropertyGroup")
            self._add_extra_options_to_node(target, n)
            if not is_library:
                n.add("LinkIncremental", c == "Debug")
            # TODO: add TargetName only if it's non-default
            n.add("TargetName", target[target.type.basename_prop])
            # TODO: handle the defaults in a nicer way
            if target["outputdir"].as_native_path(paths_info) != paths_info.builddir_abs:
                n.add("OutDir", target["outputdir"])
            if n.has_children():
                n["Condition"] = "'$(Configuration)|$(Platform)'=='%s|Win32'" % c
            root.add(n)

        for c in configs:
            n = Node("ItemDefinitionGroup")
            n["Condition"] = "'$(Configuration)|$(Platform)'=='%s|Win32'" % c
            n_cl = Node("ClCompile")
            self._add_extra_options_to_node(target, n_cl)
            n_cl.add("WarningLevel", "Level3")
            if c == "Debug":
                n_cl.add("Optimization", "Disabled")
                std_defs = "WIN32;_DEBUG"
            else:
                n_cl.add("Optimization", "MaxSpeed")
                n_cl.add("FunctionLevelLinking", True)
                n_cl.add("IntrinsicFunctions", True)
                std_defs = "WIN32;NDEBUG"
            if is_exe:
                std_defs += ";_CONSOLE"
            if is_library:
                std_defs += ";_LIB"
            if is_dll:
                std_defs += ";_USRDLL;%s_EXPORTS" % target.name.upper()
            std_defs += ";%(PreprocessorDefinitions)"
            defs = bkl.expr.ListExpr(
                            list(target["defines"]) +
                            [bkl.expr.LiteralExpr(std_defs)])
            n_cl.add("PreprocessorDefinitions", defs)
            n_cl.add("MultiProcessorCompilation", True)
            n_cl.add("MinimalRebuild", False)
            n_cl.add("AdditionalIncludeDirectories", target["includedirs"])

            crt = "MultiThreaded"
            if c == "Debug":
                crt += "Debug"
            if target["win32-crt-linkage"] == "dll":
                crt += "DLL"
            n_cl.add("RuntimeLibrary", crt)

            # Currently we don't make any distinction between preprocessor, C
            # and C++ flags as they're basically all the same at MSVS level
            # too and all go into the same place in the IDE and same
            # AdditionalOptions node in the project file.
            all_cflags = list(target["compiler-options"]) + list(target["c-compiler-options"]) + list(target["cxx-compiler-options"])
            if all_cflags:
                n_cl.add("AdditionalOptions",
                         "%s %%(AdditionalOptions)" % " ".join(x.as_py() for x in all_cflags))
            n.add(n_cl)
            n_link = Node("Link")
            self._add_extra_options_to_node(target, n_link)
            n.add(n_link)
            if is_exe:
                n_link.add("SubSystem",
                           "Windows" if target["win32-subsystem"] == "windows" else "Console")
            else:
                n_link.add("SubSystem", "Windows")
            n_link.add("GenerateDebugInformation", True)
            if c == "Release":
                n_link.add("EnableCOMDATFolding", True)
                n_link.add("OptimizeReferences", True)
            if not is_library:
                ldflags = target["link-options"]
                if ldflags:
                    n_link.add("AdditionalOptions", "%s %%(AdditionalOptions)" % " ".join(ldflags.as_py()))
            if is_library:
                libs = target["libs"]
                if libs:
                    n_lib = Node("Lib")
                    self._add_extra_options_to_node(target, n_lib)
                    n.add(n_lib)
                    n_lib.add("AdditionalDependencies", " ".join("%s.lib" % x.as_py() for x in libs))
            pre_build = target["pre-build-commands"]
            if pre_build:
                n_script = Node("PreBuildEvent")
                n_script.add("Command", "\n".join(pre_build.as_py()))
                n.add(n_script)
            post_build = target["post-build-commands"]
            if post_build:
                n_script = Node("PostBuildEvent")
                n_script.add("Command", "\n".join(post_build.as_py()))
                n.add(n_script)
            root.add(n)

        # Source files:
        items = Node("ItemGroup")
        root.add(items)
        for sfile in target.sources:
            ext = sfile.filename.get_extension()
            # FIXME: make this more solid
            if ext in ['cpp', 'cxx', 'cc', 'c']:
                items.add("ClCompile", Include=sfile.filename)
            else:
                # FIXME: handle both compilation into cpp and c files
                genfiletype = bkl.compilers.CxxFileType.get()
                genname = sfile.filename.change_extension("cpp")
                # FIXME: needs to flatten the path too
                genname.anchor = bkl.expr.ANCHOR_BUILDDIR
                ft_from = bkl.compilers.get_file_type(ext)
                compiler = bkl.compilers.get_compiler(self, ft_from, genfiletype)

                customBuild = Node("CustomBuild", Include=sfile.filename)
                customBuild.add("Command", compiler.commands(self, target, sfile.filename, genname))
                customBuild.add("Outputs", genname)
                items.add(customBuild)
                items.add("ClCompile", Include=genname)

        # Headers files:
        if target.headers:
            items = Node("ItemGroup")
            root.add(items)
            for sfile in target.headers:
                items.add("ClInclude", Include=sfile.filename)

        # Dependencies:
        target_deps = target["deps"].as_py()
        if target_deps:
            refs = Node("ItemGroup")
            root.add(refs)
            for dep_id in target_deps:
                dep = target.project.get_target(dep_id)
                depnode = Node("ProjectReference", Include=dep["%s.projectfile" % self.name])
                depnode.add("Project", dep["%s.guid" % self.name].as_py().lower())
                refs.add(depnode)

        root.add("Import", Project="$(VCTargetsPath)\\Microsoft.Cpp.targets")
        root.add("ImportGroup", Label="ExtensionTargets")

        f = OutputFile(filename, EOL_WINDOWS,
                       creator=self, create_for=target)
        f.write(codecs.BOM_UTF8)
        f.write(XmlFormatter(paths_info).format(root))
        f.commit()
        self._write_filters_file_for(filename)

        return self.Project(target.name, guid, projectfile, target_deps)

    def _set_VCTargetsPath(self, root):
        pass


    def _add_extra_options_to_node(self, target, node):
        """Add extra native options specified in vs2010.option.* properties."""
        try:
            scope = "%s.option.%s" % (self.name, node["Label"])
        except KeyError:
            if node.name == "PropertyGroup":
                scope = "%s.option" % self.name
            else:
                scope = "%s.option.%s" % (self.name, node.name)
        for var in target.variables.itervalues():
            split = var.name.rsplit(".", 1)
            if len(split) == 2 and split[0] == scope:
                node.add(str(split[1]), var.value)


    def _write_filters_file_for(self, filename):
        f = OutputFile(filename + ".filters", EOL_WINDOWS,
                       creator=self, create_for=filename)
        f.write("""\
<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="4.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup>
    <Filter Include="Source Files">
      <UniqueIdentifier>{4FC737F1-C7A5-4376-A066-2A32D752A2FF}</UniqueIdentifier>
      <Extensions>cpp;c;cc;cxx;def;odl;idl;hpj;bat;asm;asmx</Extensions>
    </Filter>
    <Filter Include="Header Files">
      <UniqueIdentifier>{93995380-89BD-4b04-88EB-625FBE52EBFB}</UniqueIdentifier>
      <Extensions>h;hpp;hxx;hm;inl;inc;xsd</Extensions>
    </Filter>
    <Filter Include="Resource Files">
      <UniqueIdentifier>{67DA6AB6-F800-4c08-8B7A-83BB121AAD01}</UniqueIdentifier>
      <Extensions>rc;ico;cur;bmp;dlg;rc2;rct;bin;rgs;gif;jpg;jpeg;jpe;resx;tiff;tif;png;wav;mfcribbon-ms</Extensions>
    </Filter>
  </ItemGroup>
</Project>
""")
        f.commit()



class VS2010Toolset(VS201xToolsetBase):
    """
    Visual Studio 2010.


    Special properties
    ------------------
    In addition to the properties described below, it's possible to specify any
    of the ``vcxproj`` properties directly in a bakefile. To do so, you have to
    set specially named variables on the target.

    The variables are prefixed with ``vs2010.option.``, followed by node name and
    property name. The following nodes are supported:

      - ``vs2010.option.Globals.*``
      - ``vs2010.option.Configuration.*``
      - ``vs2010.option.*`` (this is the unnamed ``PropertyGroup`` with
        global settings such as ``TargetName``)
      - ``vs2010.option.ClCompile.*``
      - ``vs2010.option.Link.*``
      - ``vs2010.option.Lib.*``

    Examples:

    .. code-block:: bkl

        vs2010.option.GenerateManifest = false;
        vs2010.option.Link.CreateHotPatchableImage = Enabled;

    """
    name = "vs2010"

    version = 10
    proj_versions = [10]
    # don't set to "v100" because vs2010 doesn't explicitly set it by default:
    platform_toolset = None
    Solution = VS2010Solution
    Project = VS2010Project



class VS11Solution(VS2010Solution):
    format_version = "12.00"
    human_version = "11"


class VS11Project(VS2010Project):
    version = 11


class VS11Toolset(VS201xToolsetBase):
    """
    Visual Studio 11.


    Special properties
    ------------------
    This toolset supports the same special properties that
    :ref:`ref_toolset_vs2010`. The only difference is that they are prefixed
    with ``vs11.option.`` instead of ``vs2010.option.``, i.e. the nodes are:

      - ``vs11.option.Globals.*``
      - ``vs11.option.Configuration.*``
      - ``vs11.option.*`` (this is the unnamed ``PropertyGroup`` with
        global settings such as ``TargetName``)
      - ``vs11.option.ClCompile.*``
      - ``vs11.option.Link.*``
      - ``vs11.option.Lib.*``

    """

    name = "vs11"

    version = 11
    proj_versions = [10, 11]
    platform_toolset = "v110"
    Solution = VS11Solution
    Project = VS11Project

    def _set_VCTargetsPath(self, root):
        n = Node("PropertyGroup", Label="Globals")
        root.add(n)
        n.add(Node("VCTargetsPath",
                   "$(VCTargetsPath11)",
                   Condition="'$(VCTargetsPath11)' != '' and '$(VSVersion)' == '' and '$(VisualStudioVersion)' == ''"))

import gi
import os
import sys
from functools import reduce
from itertools import starmap
from pathlib import Path
import typing

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkSource', '3.0')  # noqa

from gi.repository import Gdk, Gtk, GObject, GtkSource, Gio

from pygdbmi.gdbcontroller import GdbController, GdbTimeoutError

CATEGORY_BREAKPOINT = "BREAKPOINT"
NAME_EXEC_MARKER = "EXEC_MAKER"
CATEGORY_EXEC_MARKER = NAME_EXEC_MARKER

default_manager = GtkSource.LanguageManager.get_default()


def get_gtk_language(lang):
    return default_manager.get_language(lang)


# FOR DEBUGGING
PROGRAMS = ["examples/first/left.c", "examples/first/right.c"]
NUM_PROGRAMS = len(PROGRAMS)


class BiGdbWindow(Gtk.ApplicationWindow):
    toolbar: Gtk.Toolbar
    code_frames: typing.List

    def __init__(self):
        Gtk.ApplicationWindow.__init__(self)
        self.connect("destroy", Gtk.main_quit)

        self.toolbar = Gtk.Toolbar()
        self.btnStart = Gtk.ToolButton(label="(Re)Start", stock_id=Gtk.STOCK_MEDIA_PLAY)
        self.toolbar.add(self.btnStart)
        self.btnStep = Gtk.ToolButton(label="s", stock_id=Gtk.STOCK_MEDIA_FORWARD)
        self.toolbar.add(self.btnStep)
        self.btnContinue = Gtk.ToolButton(label="c", stock_id=Gtk.STOCK_MEDIA_NEXT)
        self.toolbar.add(self.btnContinue)

        root = Gtk.VBox()
        debug = DebugFrame()

        def new_code_frame(n: int, filename: str):
            cf = CodeFrame()
            cf.set_gdb_callback(debug.do_gdb, n)
            cf.load(filename)
            return cf

        current_pane = 1

        def new_paned(a, b):
            nonlocal current_pane
            paned = Gtk.HPaned()
            paned.pack1(a, True, True)
            paned.pack2(b, True, True)
            paned.set_position(1200 * current_pane * 1.0 / (1 + NUM_PROGRAMS))
            current_pane += 1
            return paned

        self.code_frames = list(starmap(new_code_frame,
                                        enumerate(PROGRAMS)))

        paned_root = reduce(new_paned, [*self.code_frames, debug])

        root.pack_start(self.toolbar, False, False, 0)
        root.pack_start(paned_root, True, True, 5)
        self.add(root)

        self.btnStart.connect('clicked', self.do_start)
        self.btnContinue.connect('clicked', self.do_continue)
        self.btnStep.connect('clicked', self.do_step)

    def do_start(self, *args):
        for cf in self.code_frames: cf.gdb_run()

    def do_step(self, *args):
        for cf in self.code_frames: cf.gdb_step()

    def do_continue(self, *args):
        for cf in self.code_frames: cf.gdb_continue()


class VariablesModel(object):
    def __init__(self):
        self._store = Gtk.ListStore(str, str, str)
        self._data = dict()

    def emit(self, program: int, variable: str, value: str):
        if variable not in self._data:
            v = [None] * NUM_PROGRAMS
            v[program] = value
            self._data[variable] = v
        else:
            self._data[variable][program] = value

        self.repopulate()

    def repopulate(self):
        self._store.clear()
        for k, v in self._data.items():
            row = [k, *v]
            self._store.append(row)


class DebugFrame(Gtk.ListBox):
    def __init__(self):
        Gtk.VBox.__init__(self)

        self.expandVariables = Gtk.Expander(label="Variables")
        self.expandWatches = Gtk.Expander(label="Watches")
        self.expandStack = Gtk.Expander(label="Stack")

        self.tableModelVariables = VariablesModel()
        self.tableViewVariables = Gtk.TreeView(self.tableModelVariables._store)
        self.expandVariables.add(self.tableViewVariables)

        self.tableViewVariables.append_column(Gtk.TreeViewColumn("Name", Gtk.CellRendererText(), text=0))
        for i in range(NUM_PROGRAMS):
            self.tableViewVariables.append_column(
                Gtk.TreeViewColumn("Program#%d" % i, Gtk.CellRendererText(), text=i + 1))

        self.expandVariables.set_expanded(True)
        # self.tableModelVariables.emit(1, "abc", "def")
        # self.tableModelVariables.emit(0, "abc", "qwe")

        self.add(self.expandStack)
        self.add(self.expandWatches)
        self.add(self.expandVariables)

    def do_gdb(self, gdb: GdbController, pid: int):
        # -stack-list-locals2
        # ^done, locals=[{name = "r", type = "int", value = "0"}]
        resp = gdb.write("-stack-list-locals 2")
        print(resp)
        locals = resp[0]['payload']['locals']

        for var in locals:
            self.tableModelVariables.emit(pid, var['name'], var['value'])

        # -stack-select-frame num

        # -stack-info-depth
        # stack_depth = gdb.write("-stack-info-depth")[0]['payload']['depth']
        # print("Stack depth: ", stack_depth)


def get_executable_path(path: Path):
    parent = path.parent
    return parent / path.stem


# TODO: Extend for multiple files and breakpoints
#       * Gtk Notebook (Tabbed Panes with GtkSource.View)
#       * self._breakpoints: (File x Line) -> Mark
class CodeFrame(Gtk.VBox):
    location: Path
    toolbar: Gtk.Toolbar
    code: GtkSource.Buffer
    _gdb_callback = None
    _gdb_callback_arg: object
    _gdb: GdbController
    _exec_mark: GtkSource.Mark = None

    def __init__(self):
        Gtk.VBox.__init__(self)
        self._gdb = None
        self.toolbar = Gtk.Toolbar()
        self.btnLoad = Gtk.ToolButton(label="Load", stock_id=Gtk.STOCK_OPEN)
        self.btnSave = Gtk.ToolButton(label="Save", stock_id=Gtk.STOCK_SAVE)
        self.toolbar.add(self.btnLoad)
        self.toolbar.add(self.btnSave)

        self.toolbar.add(Gtk.SeparatorToolItem())

        self.btnStart = Gtk.ToolButton(label="(Re)Start", stock_id=Gtk.STOCK_MEDIA_PLAY)
        self.toolbar.add(self.btnStart)

        self.btnStep = Gtk.ToolButton(label="s", stock_id=Gtk.STOCK_MEDIA_FORWARD)
        self.toolbar.add(self.btnStep)

        self.btnContinue = Gtk.ToolButton(label="c", stock_id=Gtk.STOCK_MEDIA_NEXT)
        self.toolbar.add(self.btnContinue)

        self.editor = GtkSource.View(
            monospace=True,
            show_line_numbers=True,
            show_line_marks=True,
            tab_width=4,
            auto_indent=True,
            insert_spaces_instead_of_tabs=True,
            show_right_margin=True,
            smart_backspace=True,
            highlight_current_line=True)

        self.code = GtkSource.Buffer(
            language=get_gtk_language("cpp"))

        self.editor.set_buffer(self.code)

        # register breakpoints
        mark_attributes = GtkSource.MarkAttributes()
        mark_attributes.set_icon_name(Gtk.STOCK_STOP)
        self.editor.set_mark_attributes(CATEGORY_BREAKPOINT, mark_attributes, 1)

        # register exec marker
        mark_attributes = GtkSource.MarkAttributes()
        mark_attributes.set_icon_name(Gtk.STOCK_GO_FORWARD)
        mark_attributes.set_background(Gdk.RGBA(0, 1, 0, 1))
        self.editor.set_mark_attributes(CATEGORY_EXEC_MARKER, mark_attributes, 0)

        self.editor.connect("line-mark-activated", self.on_line_mark)

        self.pack_start(self.toolbar, expand=False, fill=False, padding=0)
        self.pack_start(self.editor, expand=True, fill=True, padding=2)

        self.btnLoad.connect("clicked", self.load_interactive)
        self.btnSave.connect("clicked", self.save_interactive)

        self.btnStart.connect("clicked", self.gdb_run)
        self.btnStep.connect("clicked", self.gdb_step)
        self.btnContinue.connect("clicked", self.gdb_continue)

        self._breakpoints = dict()

    def on_line_mark(self, view, iter, event: Gdk.Event):
        # print(event.get_button().button == Gdk.BUTTON_PRIMARY,
        #      event.get_button(), Gdk.BUTTON_PRIMARY)

        if event.get_button().button == Gdk.BUTTON_PRIMARY:
            line = iter.get_line()
            if line in self._breakpoints:
                self._disable_breakpoint(iter)
            else:
                self._enable_breakpoint(iter)
        elif event.get_button() == Gdk.BUTTON_SECONDARY:
            pass  # TODO Support for SYNC points

    def _disable_breakpoint(self, iter):
        mark = self._breakpoints[iter.get_line()]
        self.code.delete_mark(mark)
        del self._breakpoints[iter.get_line()]

    def _enable_breakpoint(self, iter):
        mark = self.code.create_source_mark(None, CATEGORY_BREAKPOINT, iter)
        mark.set_visible(True)
        self._breakpoints[iter.get_line()] = mark

    def gdb_run(self, *args):
        if self._gdb:
            # TODO ask user
            self._gdb.exit()
            self._gdb = None

        if not self._gdb:  # start new instance
            self._gdb = GdbController(verbose=True)
            self._gdb.write("-file-exec-and-symbols %s" % get_executable_path(self.location))
            responses = self._gdb.write('-file-list-exec-source-files')  # ['files']

            # Lines <-> PC
            # responses = self._gdb.write("-sym-erubbol-list-lines %s" % self.location)

            # Transfer breakpoints
            fil = self.location.name
            for line in self._breakpoints:
                self._gdb.write("-break-insert %s:%d" % (fil, 1 + line))
            self._gdb.write("-break-insert main")

            # Read breakpoints
            # TODO remove breakpoints and display the correct breakpoints from gdb
            self._remove_all_breakpoints()
            response = self._gdb.write("-break-list")
            for breakpoint in response[0]['payload']['BreakpointTable']['body']:
                line = int(breakpoint['line'])
                iter = self.code.get_iter_at_line(line - 1)
                self._enable_breakpoint(iter)

            # GDB start debugging, should stop on main
            self._gdb.write('-exec-run')
            self._gdb_notify_callback()

    def _remove_all_breakpoints(self):
        for v in self._breakpoints.values():
            self.code.delete_mark(v)
        del self._breakpoints
        self._breakpoints = dict()

    def gdb_step(self, *args):
        if self._gdb:
            self._gdb.write("-exec-step")
            self._gdb_notify_callback()

    def gdb_continue(self, *args):
        if self._gdb:
            self._gdb.write("-exec-continue")
            self._gdb_notify_callback()

    def _gdb_update_exec_mark(self):
        if self._exec_mark is not None:
            self.code.delete_mark(self._exec_mark)
        # get current line
        response = self._gdb.write("-stack-info-frame")
        line = int(response[0]['payload']['frame']['line'])
        print(line)
        self._exec_mark = self.code.create_source_mark(NAME_EXEC_MARKER, CATEGORY_EXEC_MARKER,
                                                       self.code.get_iter_at_line(line - 1))

    def _gdb_notify_callback(self):
        self._gdb_update_exec_mark()
        # ^done,frame={level="0",addr="0x00000000004004cb",func="main",file="left.c",fullname="/home/weigl/work/bigdb/left.c",line="17"}
        self._gdb_callback(self._gdb, self._gdb_callback_arg)

        # -stack-list-frames
        # ^done,stack=[frame={level="0",addr="0x0000000000400495",func="f",file="left.c",fullname="/home/weigl/work/bigdb/left.c",line="4"},frame={level="1",addr="0x00000000004004da",func="main",file="left.c",fullname="/home/weigl/work/bigdb/left.c",line="17"}]

    def load_interactive(self, *args):
        dialog = Gtk.FileChooserDialog("Load file", self.get_toplevel(),
                                       Gtk.FileChooserAction.OPEN,
                                       (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.load(dialog.get_filename())
        elif response == Gtk.ResponseType.CANCEL:
            print("Cancel clicked")
        dialog.destroy()

    def save_interactive(self, *args):
        print(self.location)
        if self.location:
            self.save()
        else:
            dialog = Gtk.FileChooserDialog("Save file", self.get_toplevel(),
                                           Gtk.FileChooserAction.SAVE,
                                           (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                            Gtk.STOCK_SAVE_AS, Gtk.ResponseType.OK))
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                self.location = Path(dialog.get_filename())
                self.save()
            elif response == Gtk.ResponseType.CANCEL:
                print("Cancel clicked")
            dialog.destroy()

    def load(self, filename):
        with open(filename) as fh:
            self.code.set_text(fh.read())
            self.location = Path(filename)
            lang = get_gtk_language(self.location.suffix[1:])
            self.code.set_language(lang)

    def save(self):
        if self.location:
            self.location.write_text(self.code.get_text())
            os.system("(cd %s; make)" % self.location.parent)

    def set_gdb_callback(self, func, arg=None):
        self._gdb_callback = func
        self._gdb_callback_arg = arg


def start_gui():
    window = BiGdbWindow()
    window.set_default_size(1200, 800)
    window.show_all()
    Gtk.main()

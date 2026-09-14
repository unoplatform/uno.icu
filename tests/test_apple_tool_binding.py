"""FIVE-1/FIVE-2 fixture regressions. No native tools or SDKs are executed."""
import json
from pathlib import Path
import shlex
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eng"))
import apple_icu as a
import provenance as p
from test_full_five import object_file

CONFIGS = ("config/icucross.mk", "config/icucross.inc", "icudefs.mk", "Makefile",
           "config/mh-darwin", "config.status", "config.log")
TOOLS = ("genrb", "genccode", "gencmn", "icupkg", "pkgdata", "gencnval", "genbrk", "gendict")
LIBRARIES = ("lib/libicuuc.dylib", "lib/libicui18n.dylib", "lib/libicutu.dylib",
             "stubdata/libicudata.dylib")


def host_fixture(root):
    for name in CONFIGS:
        p.write_new(root / name, b"# fixture host configuration\n")
    executable = bytearray(object_file(1))
    struct.pack_into("<I", executable, 12, 2)
    for name in TOOLS:
        p.write_new(root / "bin" / name, executable)
    library = bytearray(executable)
    struct.pack_into("<I", library, 12, 6)
    for name in LIBRARIES:
        p.write_new(root / name, library)


def with_dependency(data, name):
    payload = name.encode() + b"\0"
    size = (24 + len(payload) + 7) & ~7
    command = struct.pack("<IIIIII", 0xC, size, 24, 0, 0, 0) + payload
    command += bytes(size - len(command))
    result = bytearray(data + command)
    count, length = struct.unpack_from("<II", result, 16)
    struct.pack_into("<II", result, 16, count + 1, length + size)
    return bytes(result)


def sdk_fixture():
    return {"tools": {name: {"path": "/selected SDK tools/" + name, "sha256": "a" * 64}
                      for name in ("clang", "clang++", "ar", "ranlib", "lipo")}}


def configuration_fixture(root, sdk, host="/host/source"):
    env = {name: shlex.quote(sdk["tools"][tool]["path"])
           for name, tool in (("CC", "clang"), ("CXX", "clang++"), ("AR", "ar"), ("RANLIB", "ranlib"))}
    p.write_new(root / "icudefs.mk",
                ("\n".join(f"{name} = {value}" for name, value in env.items()) + "\nARFLAGS = r\n").encode())
    p.write_new(root / "config/mh-darwin", b"ARFLAGS += -c\n")
    for name in ("Makefile", "common/Makefile", "data/Makefile"):
        p.write_new(root / name, b"# fixture configured makefile\n")
    p.write_new(root / "data/pkgdataMakefile", b"# fixture matching locked pkgdata template\n")
    p.write_new(root / "data/rules.mk", b"$(INVOKE) $(TOOLBINDIR)/gencnval\n$(INVOKE) $(TOOLBINDIR)/genbrk\n$(INVOKE) $(TOOLBINDIR)/gendict\n$(INVOKE) $(TOOLBINDIR)/genrb\n$(INVOKE) $(TOOLBINDIR)/icupkg\n")
    p.write_new(root / "data/icupkg.inc",
                (f"AR={env['AR']}\nARFLAGS=r -c\nRANLIB={env['RANLIB']}\nCOMPILE={env['CC']} -c fixture.c\n").encode())
    values = {**env, "ARFLAGS": "r -c", "TOOLBINDIR": host + "/bin",
              "TOOLLIBDIR": host + "/lib", "cross_buildroot": host,
              "INVOKE": f"DYLD_LIBRARY_PATH={host}/lib:{host}/stubdata:{host}/tools/ctestfw:$DYLD_LIBRARY_PATH",
              "PKGDATA_INVOKE": f"DYLD_LIBRARY_PATH={host}/stubdata:{host}/tools/ctestfw:{host}/lib:$DYLD_LIBRARY_PATH"}
    root_output = "\n".join("UNO_ICU_TOOL_" + key + "=" + value for key, value in values.items())
    data_values = {**values, "PKGDATA_OPTS": "-O ../data/icupkg.inc",
                   "PKGDATA": host + "/bin/pkgdata -O ../data/icupkg.inc -q"}
    data_output = "\n".join("UNO_ICU_TOOL_" + key + "=" + value for key, value in data_values.items())
    return root_output, data_output


class AppleHostInputBinding(unittest.TestCase):
    def test_every_selected_required_host_input_is_mandatory(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(a.os, "access", return_value=True):
            root = Path(temp)
            host_fixture(root)
            a.host_identity(root)
            required = (*CONFIGS, *("bin/" + name for name in TOOLS), *LIBRARIES)
            missed = []
            for name in required:
                path = root / name
                original = path.read_bytes()
                path.unlink()
                try:
                    a.host_identity(root)
                    missed.append(name)
                except ValueError:
                    pass
                path.write_bytes(original)
            self.assertEqual([], missed)

    def test_changing_each_consumed_file_changes_the_snapshot(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(a.os, "access", return_value=True):
            root = Path(temp)
            host_fixture(root)
            before = a.host_identity(root)
            missed = []
            for name in (*CONFIGS, *("bin/" + name for name in TOOLS), *LIBRARIES):
                path = root / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n# fixture change\n")
                after = a.host_identity(root)
                if before == after:
                    missed.append(name)
                path.write_bytes(original)
            self.assertEqual([], missed)

    def test_selected_filter_includes_dictionary_alias_and_break_generators(self):
        required = a.selected_host_tools((p.ROOT / "src/cldr_data/filters.json").read_bytes())
        self.assertTrue({"gencnval", "genbrk", "gendict", "genrb", "icupkg", "pkgdata"} <= set(required))
        changed = json.loads((p.ROOT / "src/cldr_data/filters.json").read_text())
        changed["featureFilters"]["unknown_generator"] = "include"
        with self.assertRaises(ValueError):
            a.selected_host_tools(p.json_bytes(changed))

    def test_consumed_versioned_library_and_transitive_dependency_are_mandatory(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(a.os, "access", return_value=True):
            root = Path(temp)
            host_fixture(root)
            tool = root / "bin/genbrk"
            tool.write_bytes(with_dependency(tool.read_bytes(), "libicutu.77.dylib"))
            util = root / "lib/libicutu.77.dylib"
            util.write_bytes(with_dependency((root / "lib/libicutu.dylib").read_bytes(), "libicuuc.77.dylib"))
            common = root / "lib/libicuuc.77.dylib"
            common.write_bytes(with_dependency((root / "lib/libicuuc.dylib").read_bytes(), "/usr/lib/libSystem.B.dylib"))
            result = a.host_identity(root)
            self.assertEqual("lib/libicutu.77.dylib", result["dependencyBindings"]["bin/genbrk"]["bin/genbrk"]["libicutu.77.dylib"])
            self.assertEqual("lib/libicuuc.77.dylib", result["dependencyBindings"]["bin/genbrk"]["lib/libicutu.77.dylib"]["libicuuc.77.dylib"])
            before = common.read_bytes()
            common.write_bytes(before + b"changed")
            self.assertNotEqual(result, a.host_identity(root))
            common.unlink()
            with self.assertRaisesRegex(ValueError, "consumed"):
                a.host_identity(root)

    def test_host_library_search_order_matches_normal_and_pkgdata_invocation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(a.os, "access", return_value=True):
            root = Path(temp)
            host_fixture(root)
            for name in ("genrb", "pkgdata"):
                file = root / "bin" / name
                file.write_bytes(with_dependency(file.read_bytes(), "libicudata.77.dylib"))
            data = (root / "stubdata/libicudata.dylib").read_bytes()
            p.write_new(root / "lib/libicudata.77.dylib", data + b"lib")
            p.write_new(root / "stubdata/libicudata.77.dylib", data + b"stub")
            identity = a.host_identity(root)
            self.assertEqual("lib/libicudata.77.dylib", identity["dependencyBindings"]["bin/genrb"]["bin/genrb"]["libicudata.77.dylib"])
            self.assertEqual("stubdata/libicudata.77.dylib", identity["dependencyBindings"]["bin/pkgdata"]["bin/pkgdata"]["libicudata.77.dylib"])

    def test_unreviewed_non_system_host_dependency_and_local_config_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(a.os, "access", return_value=True):
            root = Path(temp)
            host_fixture(root)
            file = root / "bin/genrb"
            original = file.read_bytes()
            file.write_bytes(with_dependency(original, "/unreviewed/libother.dylib"))
            with self.assertRaises(ValueError):
                a.host_identity(root)
            file.write_bytes(original)
            p.write_new(root / "icudefs.local", b"AR=/wrong/ar\n")
            with self.assertRaises(ValueError):
                a.host_identity(root)

    def test_host_input_type_and_executable_permission_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            host_fixture(root)
            with patch.object(a.os, "access", return_value=False), self.assertRaisesRegex(ValueError, "executable"):
                a.host_identity(root)
            with patch.object(a.os, "access", return_value=True):
                path = root / "bin/genbrk"
                original = path.read_bytes()
                for data in (b"not executable", object_file(1, "x86_64"), object_file(2)):
                    path.write_bytes(data)
                    with self.assertRaises(ValueError):
                        a.host_identity(root)
                path.write_bytes(original)
                config = root / "config/icucross.inc"
                config.unlink()
                config.mkdir()
                with self.assertRaises(ValueError):
                    a.host_identity(root)

    def test_retained_host_configuration_bytes_must_match_snapshot(self):
        from test_full_five import apple_fixture
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            apple_fixture(root, "a" * 64, p.load_lock(), "c" * 64)
            for name in CONFIGS:
                path = root / "host-config" / name
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "host configuration"):
                    a.verify_receipt(root, "a" * 64, p.load_lock(), "c" * 64)
                path.write_bytes(original)


class AppleArchiverBinding(unittest.TestCase):
    def test_selected_archiver_and_indexer_override_conflicting_environment(self):
        parent = {"PATH": "/unchanged/path", "AR": "/wrong/ar", "RANLIB": "/wrong/ranlib",
                  "ARFLAGS": "bad", "MAKEFLAGS": "-e", "PKGDATA_OPTS": "-O /wrong/config",
                  "MAKEFILES": "/wrong/makefile", "GNUMAKEFLAGS": "-e",
                  "DYLD_LIBRARY_PATH": "/wrong/lib", "DYLD_IMAGE_SUFFIX": "_wrong"}
        selected = a.variant_environment(parent, sdk_fixture(), "/selected/filter.json")
        self.assertEqual(parent["PATH"], selected["PATH"])
        self.assertEqual(["/selected SDK tools/ar"], shlex.split(selected["AR"]))
        self.assertEqual(["/selected SDK tools/ranlib"], shlex.split(selected["RANLIB"]))
        self.assertEqual("", selected["ARFLAGS"])
        self.assertNotIn("MAKEFLAGS", selected)
        self.assertNotIn("MAKEFILES", selected)
        self.assertNotIn("GNUMAKEFLAGS", selected)
        self.assertNotIn("PKGDATA_OPTS", selected)
        self.assertNotIn("DYLD_LIBRARY_PATH", selected)
        self.assertNotIn("DYLD_IMAGE_SUFFIX", selected)

    def test_generated_icudefs_make_and_pkgdata_bind_selected_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outputs = configuration_fixture(root, sdk_fixture())
            result = a.verify_tool_configuration(root, sdk_fixture(), "/host/source", *outputs)
            self.assertEqual("/selected SDK tools/ar", result["selectedTools"]["AR"])
            self.assertEqual("/selected SDK tools/ranlib", result["selectedTools"]["RANLIB"])
            self.assertEqual(["r", "-c"], result["archiveFlags"])

    def test_each_generated_tool_substitution_or_ambiguous_path_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outputs = configuration_fixture(root, sdk_fixture())
            for file, old, new in (
                ("icudefs.mk", "AR = '/selected SDK tools/ar'", "AR = /wrong/ar"),
                ("icudefs.mk", "RANLIB = '/selected SDK tools/ranlib'", "RANLIB = /wrong/ranlib"),
                ("data/icupkg.inc", "AR='/selected SDK tools/ar'", "AR=/selected SDK tools/ar"),
                ("data/icupkg.inc", "RANLIB='/selected SDK tools/ranlib'", "RANLIB=:"),
                ("data/icupkg.inc", "ARFLAGS=r -c", "ARFLAGS=q"),
            ):
                path = root / file
                original = path.read_bytes()
                path.write_bytes(original.replace(old.encode(), new.encode()))
                with self.subTest(file=file, new=new), self.assertRaises(ValueError):
                    a.verify_tool_configuration(root, sdk_fixture(), "/host/source", *outputs)
                path.write_bytes(original)
            with self.assertRaises(ValueError):
                a.verify_tool_configuration(root, sdk_fixture(), "/host/source",
                                            outputs[0].replace("UNO_ICU_TOOL_AR=", "UNO_ICU_TOOL_WRONG="), outputs[1])
            with self.assertRaises(ValueError):
                a.verify_tool_configuration(root, sdk_fixture(), "/host/source", outputs[0],
                                            outputs[1].replace("-O ../data/icupkg.inc", "-O /wrong/config"))

    def test_unknown_generated_host_tool_and_local_overrides_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outputs = configuration_fixture(root, sdk_fixture())
            (root / "data/rules.mk").write_bytes(b"$(INVOKE) $(TOOLBINDIR)/unrecorded_tool\n")
            with self.assertRaises(ValueError):
                a.verify_tool_configuration(root, sdk_fixture(), "/host/source", *outputs)
            (root / "data/rules.mk").write_bytes(b"$(INVOKE) $(TOOLBINDIR)/genrb\n")
            p.write_new(root / "icudefs.local", b"AR=/wrong/ar\n")
            with self.assertRaises(ValueError):
                a.verify_tool_configuration(root, sdk_fixture(), "/host/source", *outputs)

    def test_make_metadata_probe_contains_no_archiver_execution_or_shell_interpolation(self):
        for root_scope in (True, False):
            text = a.tool_probe_makefile(root_scope)
            self.assertIn("$(info UNO_ICU_TOOL_AR=$(AR))", text)
            self.assertIn("$(info UNO_ICU_TOOL_RANLIB=$(RANLIB))", text)
            self.assertNotIn("\t$(AR) ", text)
            self.assertNotIn("\t$(RANLIB) ", text)
            self.assertNotIn("echo", text)

    def test_receipt_rejects_each_changed_config_or_evaluated_selection(self):
        from test_full_five import apple_fixture
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            apple_fixture(root, "a" * 64, p.load_lock(), "c" * 64)
            variant = root / "variants/ios-arm64"
            for phase in ("configuration", "configuration-final"):
                for name in a.CONFIGURATION_FILES:
                    path = variant / phase / name
                    original = path.read_bytes()
                    path.write_bytes(original + b"\n# changed\n")
                    with self.subTest(phase=phase, name=name), self.assertRaises(ValueError):
                        a.verify_receipt(root, "a" * 64, p.load_lock(), "c" * 64)
                    path.write_bytes(original)
            for name in ("make-evaluated-before.txt", "make-evaluated-after.txt",
                         "data-make-evaluated-before.txt", "data-make-evaluated-after.txt"):
                path = variant / name
                original = path.read_bytes()
                path.write_bytes(original.replace(b"UNO_ICU_TOOL_AR=/fixture/ar", b"UNO_ICU_TOOL_AR=/wrong/ar"))
                with self.subTest(name=name), self.assertRaises(ValueError):
                    a.verify_receipt(root, "a" * 64, p.load_lock(), "c" * 64)
                path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()

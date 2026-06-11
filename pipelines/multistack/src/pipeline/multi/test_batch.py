"""Mocked orchestration tests for the v2 `batch` flow (kb-multi-image2.md P1).

No GPU / model weights required: `generate_candidates`, `invoke_zimage`, and
session-manifest ingest are patched with in-process fakes (same approach the
Phase A tests used -- see kb-multi-image.md). These lock P1 behaviour before
the P2 shared-module refactor moves code around.

stdlib only (this venv has no pytest). Run with:

    python src/pipeline/multi/test_batch.py
    # or:  python -m unittest pipeline.multi.test_batch  (with PYTHONPATH=src)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make `pipeline.*` resolve when invoked from any cwd.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[2]))  # repo_root/src

from pipeline.multi import arch_batch                       # noqa: E402
from pipeline.multi.candidates import Candidate             # noqa: E402
from pipeline.multi.run_pipeline import (  # noqa: E402
    _build_parser, _cmd_batch, _looks_like_backend, _model_flag_error,
    _optin_error, _provided_long_opts,
)


def _fake_pool() -> list[Candidate]:
    """3 ok + 1 failed, spread across pipelines/seeds."""
    return [
        Candidate("flux2", 42, 0, "ok", "/fake/flux2_s42.png", "/fake/flux2_s42.json", 1.0),
        Candidate("sd35", 42, 1, "ok", "/fake/sd35_s42.png", "/fake/sd35_s42.json", 1.0),
        Candidate("zimage", 42, 2, "ok", "/fake/zimage_s42.png", "/fake/zimage_s42.json", 1.0),
        Candidate("flux2", 43, 3, "failed", "", "", 0.0, "simulated OOM"),
    ]


def _ok_invoke(call_log):
    """Fake of `pipeline._img2img.backends.run_img2img` (shared dispatcher)."""
    def _inv(image_path, **kw):
        call_log.append({"image_path": image_path, **kw})
        n = len(call_log)
        return {"returncode": 0, "output_path": f"/fake/clean_{n}.png",
                "sub_manifest_path": f"/fake/clean_{n}.json",
                "subprocess_duration_s": 0.5, "stderr": ""}
    return _inv


def _flaky_invoke(call_log, fail_on=2):
    def _inv(image_path, **kw):
        call_log.append({"image_path": image_path, **kw})
        n = len(call_log)
        if n == fail_on:
            return {"returncode": 1, "output_path": "", "sub_manifest_path": "",
                    "subprocess_duration_s": 0.1, "stderr": "boom"}
        return {"returncode": 0, "output_path": f"/fake/clean_{n}.png",
                "sub_manifest_path": f"/fake/clean_{n}.json",
                "subprocess_duration_s": 0.5, "stderr": ""}
    return _inv


class BatchOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = Path(self._tmp.name)
        self.dirs = dict(
            output_dir=t / "out",
            intermediate_root=t / "inter",
            sessions_dir=t / "sessions",
        )
        self.calls: list = []
        # Default sidecar auto-detect: nothing detected (force fallbacks).
        self._detect = lambda p: {"backend": None, "seed": None, "model": None,
                                  "prompt": None, "manifest_path": None}

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, invoke, **kw):
        """Patch the external touchpoints arch_batch reaches, then run."""
        with mock.patch.object(arch_batch, "generate_candidates",
                               lambda **_: _fake_pool()), \
             mock.patch.object(arch_batch, "ingest_pipeline_manifest",
                               lambda *a, **k: []), \
             mock.patch.object(arch_batch, "detect_source_pipeline",
                               lambda p: self._detect(p)), \
             mock.patch.object(arch_batch, "run_img2img", invoke):
            return arch_batch.run_batch(prompt="x", seed=42, **self.dirs, **kw)

    def test_ideate_only_has_no_clean_stage(self):
        m = self._run(_ok_invoke(self.calls), do_clean=False)
        self.assertEqual([s.name for s in m.stages], ["ideate"])
        ide = m.stages[0]
        self.assertEqual(ide.status, "completed")
        self.assertEqual(ide.outputs["candidate_count"], 4)
        self.assertEqual(ide.outputs["succeeded"], 3)
        self.assertEqual(ide.outputs["failed"], 1)
        self.assertEqual(self.calls, [])  # clean never invoked

    def test_clean_all_fans_over_successful_pool(self):
        m = self._run(_ok_invoke(self.calls), do_clean=True)
        self.assertEqual([s.name for s in m.stages], ["ideate", "clean"])
        clean = m.stages[1]
        self.assertEqual(clean.status, "completed")
        self.assertEqual(len(self.calls), 3)            # only the 3 ok candidates
        self.assertEqual(clean.outputs["succeeded"], 3)
        self.assertEqual(clean.outputs["failed"], 0)
        self.assertEqual(len(clean.outputs["cleaned"]), 3)
        self.assertTrue(all(c["prompt"] == "x" for c in self.calls))  # defaults to batch prompt

    def test_clean_partial_failure_is_nonfatal(self):
        m = self._run(_flaky_invoke(self.calls, fail_on=2), do_clean=True)
        clean = m.stages[1]
        self.assertEqual(clean.status, "completed")     # stage completes despite 1 fail
        self.assertEqual(clean.outputs["succeeded"], 2)
        self.assertEqual(clean.outputs["failed"], 1)
        self.assertEqual(
            sorted(c["status"] for c in clean.outputs["cleaned"]),
            ["failed", "ok", "ok"],
        )
        self.assertTrue(list(Path(self.dirs["output_dir"]).glob("multi_batch_*.json")))

    def test_clean_backend_defaults_to_zimage(self):
        m = self._run(_ok_invoke(self.calls), do_clean=True)
        self.assertTrue(all(c["backend"] == "zimage-img2img" for c in self.calls))
        self.assertTrue(all(c["cfg_normalization"] is True for c in self.calls))
        self.assertEqual(m.stages[1].inputs["backend"], "zimage-img2img")
        self.assertEqual(m.stages[1].outputs["backend"], "zimage-img2img")
        self.assertTrue(all(r["backend"] == "zimage-img2img"
                            for r in m.stages[1].outputs["cleaned"]))

    def test_clean_backend_override_threads_through(self):
        m = self._run(_ok_invoke(self.calls), do_clean=True,
                       clean_backend="sd35-img2img", clean_model="sd3.5-medium")
        self.assertTrue(all(c["backend"] == "sd35-img2img" for c in self.calls))
        self.assertTrue(all(c["model_name"] == "sd3.5-medium" for c in self.calls))
        self.assertEqual(m.stages[1].inputs["backend"], "sd35-img2img")
        self.assertEqual(m.stages[1].outputs["backend"], "sd35-img2img")

    # ---- P4: polish-all -------------------------------------------------

    def test_polish_all_over_pool_no_clean(self):
        m = self._run(_ok_invoke(self.calls), do_polish=True)
        self.assertEqual([s.name for s in m.stages], ["ideate", "polish"])
        pol = m.stages[1]
        self.assertEqual(pol.status, "completed")
        self.assertEqual(len(self.calls), 3)            # 3 ok candidates
        self.assertEqual(pol.outputs["succeeded"], 3)
        # no clean -> polish input is the raw ideate candidate
        self.assertEqual(
            sorted(c["image_path"] for c in self.calls),
            ["/fake/flux2_s42.png", "/fake/sd35_s42.png", "/fake/zimage_s42.png"],
        )

    def test_polish_backend_resolution_explicit_sidecar_fallback(self):
        # 1) explicit --polish-backend wins over sidecar
        self._detect = lambda p: {"backend": "zimage-img2img", "seed": 9,
                                  "model": None, "prompt": "sp",
                                  "manifest_path": None}
        self._run(_ok_invoke(self.calls), do_polish=True,
                  polish_backend="flux2-img2img")
        self.assertTrue(all(c["backend"] == "flux2-img2img" for c in self.calls))

        # 2) else sidecar-detected backend/seed/prompt
        self.calls.clear()
        self._run(_ok_invoke(self.calls), do_polish=True)
        self.assertTrue(all(c["backend"] == "zimage-img2img" for c in self.calls))
        self.assertTrue(all(c["seed"] == 9 for c in self.calls))       # sidecar seed
        self.assertTrue(all(c["prompt"] == "sp" for c in self.calls))  # sidecar prompt

        # 3) nothing detected -> sd35-img2img + batch prompt + candidate seed
        self.calls.clear()
        self._detect = lambda p: {"backend": None, "seed": None, "model": None,
                                  "prompt": None, "manifest_path": None}
        self._run(_ok_invoke(self.calls), do_polish=True)
        self.assertTrue(all(c["backend"] == "sd35-img2img" for c in self.calls))
        self.assertTrue(all(c["prompt"] == "x" for c in self.calls))   # batch prompt
        self.assertTrue(all(c["seed"] == 42 for c in self.calls))      # candidate seed

    def test_polish_uses_clean_output_when_clean_ran(self):
        m = self._run(_ok_invoke(self.calls), do_clean=True, do_polish=True)
        self.assertEqual([s.name for s in m.stages], ["ideate", "clean", "polish"])
        clean_calls = [c for c in self.calls if "cfg_normalization" in c]
        polish_calls = [c for c in self.calls if "cfg_normalization" not in c]
        self.assertEqual(len(clean_calls), 3)
        self.assertEqual(len(polish_calls), 3)
        # polish consumes the clean OUTPUT, not the raw ideate image
        self.assertTrue(all(c["image_path"].startswith("/fake/clean_")
                            for c in polish_calls))

    def test_polish_partial_failure_is_nonfatal(self):
        m = self._run(_flaky_invoke(self.calls, fail_on=2), do_polish=True)
        pol = m.stages[1]
        self.assertEqual(pol.status, "completed")
        self.assertEqual(pol.outputs["succeeded"], 2)
        self.assertEqual(pol.outputs["failed"], 1)

    # ---- P5: img2img batching order ------------------------------------

    @staticmethod
    def _detect_by_name():
        # Map source filename -> backend so sorted(backends) != submission
        # order, making the grouping observable.
        def d(p):
            s = str(p)
            b = ("zimage-img2img" if "flux2" in s
                 else "flux2-img2img" if "sd35" in s
                 else "sd35-img2img")
            return {"backend": b, "seed": None, "model": None,
                    "prompt": None, "manifest_path": None}
        return d

    def test_by_backend_groups_execution_records_stay_ordered(self):
        self._detect = self._detect_by_name()
        m = self._run(_ok_invoke(self.calls), do_polish=True,
                      img2img_batching="by-backend")
        self.assertEqual([c["backend"] for c in self.calls],
                         ["flux2-img2img", "sd35-img2img", "zimage-img2img"])
        self.assertEqual(
            [r["source_candidate_index"] for r in m.stages[1].outputs["polished"]],
            [0, 1, 2])  # records always candidate-ordered

    def test_per_image_preserves_submission_order(self):
        self._detect = self._detect_by_name()
        m = self._run(_ok_invoke(self.calls), do_polish=True,
                      img2img_batching="per-image")
        self.assertEqual([c["backend"] for c in self.calls],
                         ["zimage-img2img", "flux2-img2img", "sd35-img2img"])
        self.assertEqual(
            [r["source_candidate_index"] for r in m.stages[1].outputs["polished"]],
            [0, 1, 2])

    def test_records_deterministic_across_batching_modes(self):
        self._detect = self._detect_by_name()
        a = self._run(_ok_invoke([]), do_polish=True, img2img_batching="by-backend")
        b = self._run(_ok_invoke([]), do_polish=True, img2img_batching="per-image")
        shape = lambda m: [(r["source_candidate_index"], r["status"],
                            r["backend"]) for r in m.stages[1].outputs["polished"]]
        self.assertEqual(shape(a), shape(b))

    def test_invalid_batching_raises(self):
        with self.assertRaises(ValueError):
            self._run(_ok_invoke(self.calls), img2img_batching="nope")


class SidecarAugmentationTests(unittest.TestCase):
    def test_adds_module_and_is_idempotent(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "zimage_x_s0.json"
            p.write_text(json.dumps({"seed": 0, "prompt": "p"}), encoding="utf-8")
            arch_batch._augment_sidecar_module(str(p), "zimage-img2img")
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["module"], "zimage")
            self.assertEqual(data["seed"], 0)        # existing fields preserved
            self.assertEqual(data["prompt"], "p")
            arch_batch._augment_sidecar_module(str(p), "zimage-img2img")  # idempotent
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["module"], "zimage")

    def test_nonfatal_on_missing_or_bad(self):
        arch_batch._augment_sidecar_module("/no/such/file.json", "sd35-img2img")
        arch_batch._augment_sidecar_module("", "zimage-img2img")
        arch_batch._augment_sidecar_module("/x.json", "not-a-backend")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text("not json{", encoding="utf-8")
            arch_batch._augment_sidecar_module(str(p), "sd35-img2img")  # no raise


class CliSurfaceTests(unittest.TestCase):
    def test_removed_subcommands_exit_2(self):
        for removed in ("select", "compose-character"):
            with self.subTest(removed=removed):
                parser = _build_parser()
                args = parser.parse_args([removed])
                self.assertEqual(args.func(args), 2)

    def test_batch_subcommand_registered(self):
        parser = _build_parser()
        args = parser.parse_args(["batch", "--prompt", "hello"])
        self.assertIs(args.func, _cmd_batch)
        self.assertFalse(args.clean)  # opt-in

    def test_clean_backend_flag(self):
        parser = _build_parser()
        a = parser.parse_args(["batch", "--prompt", "p", "--clean",
                               "--clean-backend", "sd35-img2img"])
        self.assertEqual(a.clean_backend, "sd35-img2img")
        self.assertIsNone(a.clean_model)
        a2 = parser.parse_args(["batch", "--prompt", "p"])
        self.assertEqual(a2.clean_backend, "zimage-img2img")  # default preserves pre-P3

    def test_polish_flags(self):
        parser = _build_parser()
        a = parser.parse_args(["batch", "--prompt", "p", "--polish",
                               "--polish-backend", "zimage-img2img",
                               "--polish-strength", "0.25", "--polish-seed", "11"])
        self.assertTrue(a.polish)
        self.assertEqual(a.polish_backend, "zimage-img2img")
        self.assertEqual(a.polish_strength, 0.25)
        self.assertEqual(a.polish_seed, 11)
        a2 = parser.parse_args(["batch", "--prompt", "p"])
        self.assertFalse(a2.polish)
        self.assertIsNone(a2.polish_backend)          # auto-detect
        self.assertEqual(a2.polish_strength, 0.22)    # postproc-identical default

    def test_img2img_batching_flag(self):
        parser = _build_parser()
        a = parser.parse_args(["batch", "--prompt", "p",
                               "--img2img-batching", "per-image"])
        self.assertEqual(a.img2img_batching, "per-image")
        a2 = parser.parse_args(["batch", "--prompt", "p"])
        self.assertEqual(a2.img2img_batching, "by-backend")  # default


class OptInGuardTests(unittest.TestCase):
    """Footgun guard: clean/polish sub-flags without the master switch."""

    def test_provided_long_opts_parsing(self):
        self.assertEqual(
            _provided_long_opts(["batch", "--clean", "--clean-backend",
                                 "sd35-img2img", "--polish-strength=0.3", "x"]),
            {"--clean", "--clean-backend", "--polish-strength"})

    def test_clean_subflag_without_switch_errors(self):
        e = _optin_error({"--clean-backend", "--clean-prompt"})
        self.assertIsNotNone(e)
        self.assertIn("--clean-backend", e)
        self.assertIn("--clean not set", e)

    def test_polish_subflag_without_switch_errors(self):
        e = _optin_error({"--polish-strength"})
        self.assertIsNotNone(e)
        self.assertIn("--polish not set", e)

    def test_both_missing_lists_both(self):
        e = _optin_error({"--clean-model", "--polish-backend"})
        self.assertIn("--clean not set", e)
        self.assertIn("--polish not set", e)

    def test_none_when_switch_present_or_unused(self):
        self.assertIsNone(_optin_error({"--clean", "--clean-backend"}))
        self.assertIsNone(_optin_error({"--polish", "--polish-seed"}))
        self.assertIsNone(_optin_error({"--prompt", "--num-candidates"}))
        # clean enabled but polish sub-flag without --polish still errors
        self.assertIsNotNone(
            _optin_error({"--clean", "--clean-backend", "--polish-model"}))

    def test_cmd_batch_guard_returns_2(self):
        argv = ["prog", "batch", "--prompt", "p",
                "--clean-backend", "sd35-img2img"]
        with mock.patch("pipeline.multi.run_pipeline.sys.argv", argv):
            self.assertEqual(_cmd_batch(object()), 2)

    def test_subflag_tables_match_parser(self):
        # Drift guard: every name in the guard tables must be a real `batch`
        # option, so the tables can't silently fall out of sync.
        import argparse

        from pipeline.multi.run_pipeline import (
            _CLEAN_SUBFLAGS, _POLISH_SUBFLAGS)
        parser = _build_parser()
        sub = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction))  # noqa: SLF001
        batch = sub.choices["batch"]
        opts = {o for act in batch._actions for o in act.option_strings}  # noqa: SLF001
        for f in (*_CLEAN_SUBFLAGS, *_POLISH_SUBFLAGS):
            self.assertIn(f, opts, f"{f} is not a real `batch` option")


class ModelFlagGuardTests(unittest.TestCase):
    """Catch backend names passed to --clean-model / --polish-model."""

    def test_looks_like_backend(self):
        for v in ("zimage-img2img", "sd35-img2img", "flux2-img2img",
                  "future-img2img"):
            self.assertTrue(_looks_like_backend(v), v)
        for v in (None, "", "zimage-base", "sd3.5-medium", "flux.2-klein-4b"):
            self.assertFalse(_looks_like_backend(v), repr(v))

    def test_clean_model_backend_errors(self):
        ns = argparse.Namespace(clean_model="sd35-img2img", polish_model=None)
        e = _model_flag_error(ns)
        self.assertIsNotNone(e)
        self.assertIn("--clean-backend", e)
        self.assertIn("sd35-img2img", e)

    def test_polish_model_backend_errors(self):
        ns = argparse.Namespace(clean_model=None, polish_model="zimage-img2img")
        e = _model_flag_error(ns)
        self.assertIsNotNone(e)
        self.assertIn("--polish-backend", e)

    def test_valid_models_pass(self):
        ns = argparse.Namespace(clean_model="zimage-base",
                                polish_model="sd3.5-medium")
        self.assertIsNone(_model_flag_error(ns))
        self.assertIsNone(_model_flag_error(
            argparse.Namespace(clean_model=None, polish_model=None)))

    def test_cmd_batch_guard_returns_2_before_ideate(self):
        argv = ["prog", "batch", "--prompt", "p", "--clean",
                "--clean-model", "sd35-img2img"]
        args = _build_parser().parse_args(argv[1:])
        with mock.patch("pipeline.multi.run_pipeline.sys.argv", argv):
            self.assertEqual(_cmd_batch(args), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

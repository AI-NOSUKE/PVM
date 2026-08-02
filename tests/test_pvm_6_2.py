# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

import PVM


class _FakeICA:
    def __init__(self, components: int, fail_at: int):
        self.components = components
        self.fail_at = fail_at
        self.components_ = np.eye(components, 5, dtype=np.float32)
        self.mean_ = np.zeros(5, dtype=np.float32)

    def fit_transform(self, X):
        if self.components == self.fail_at:
            raise RuntimeError("forced convergence failure")
        return np.asarray(X[:, : self.components], dtype=np.float32)


class Pvm620Tests(unittest.TestCase):
    def test_baseline_auto_selection_uses_matching_project_name_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (PVM.history_root(root, "sample_texts") / "v001").mkdir(parents=True)

            project, exists, resolution = PVM.resolve_default_baseline_project(
                root, "customer", None,
            )
            self.assertEqual((project, exists, resolution), ("customer", False, "none"))

            project, exists, resolution = PVM.resolve_default_baseline_project(
                root, "sample_texts", None,
            )
            self.assertEqual((project, exists, resolution), ("sample_texts", True, "project"))

            project, exists, resolution = PVM.resolve_default_baseline_project(
                root, "customer", "sample_texts",
            )
            self.assertEqual((project, exists, resolution), ("sample_texts", True, "explicit"))

    def test_restore_requires_project_or_baseline_from(self):
        args = SimpleNamespace(baseline_from=None, project=None)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(PVM.BaselineSelectionError, r"baseline 名を推測しません"):
                PVM._restore_only(Path(td), args)

    def test_windows_utf8_restart_command_preserves_original_invocation(self):
        flags = SimpleNamespace(utf8_mode=0)
        with (
            patch.object(PVM.os, "name", "nt"),
            patch.object(PVM.sys, "flags", flags),
            patch.object(PVM.sys, "executable", r"C:\Python314\python.exe"),
            patch.object(PVM.sys, "orig_argv", [r"C:\Python314\python.exe", "-m", "sample", "--flag"]),
        ):
            self.assertTrue(PVM._windows_utf8_mode_required())
            self.assertEqual(
                PVM._windows_utf8_reexec_args(),
                [r"C:\Python314\python.exe", "-X", "utf8", "-m", "sample", "--flag"],
            )

    def test_import_user_gets_actionable_windows_utf8_error(self):
        flags = SimpleNamespace(utf8_mode=0)
        with patch.object(PVM.os, "name", "nt"), patch.object(PVM.sys, "flags", flags):
            with self.assertRaisesRegex(RuntimeError, r"python -X utf8"):
                PVM._require_windows_utf8_for_embedding()

    def test_windows_utf8_restart_waits_and_propagates_exit_code(self):
        flags = SimpleNamespace(utf8_mode=0)
        completed = SimpleNamespace(returncode=7)
        with (
            patch.object(PVM.os, "name", "nt"),
            patch.object(PVM.sys, "flags", flags),
            patch.object(PVM.sys, "executable", r"C:\Python314\python.exe"),
            patch.object(PVM.sys, "orig_argv", [r"C:\Python314\python.exe", "PVM.py", "--version"]),
            patch.dict(PVM.os.environ, {}, clear=True),
            patch.object(PVM.subprocess, "run", return_value=completed) as run,
        ):
            with self.assertRaises(SystemExit) as stopped:
                PVM._ensure_windows_cli_utf8()
        self.assertEqual(stopped.exception.code, 7)
        command = run.call_args.args[0]
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(command, [r"C:\Python314\python.exe", "-X", "utf8", "PVM.py", "--version"])
        self.assertEqual(child_env["PYTHONUTF8"], "1")
        self.assertEqual(child_env[PVM._WINDOWS_UTF8_REEXEC_MARKER], "1")

    def test_discovery_can_fallback_but_exact_validation_cannot(self):
        X = np.arange(60, dtype=np.float32).reshape(12, 5)

        def factory(n_components, *_args, **_kwargs):
            return _FakeICA(int(n_components), fail_at=4)

        discovery = PVM.IcaRetryConfig(
            max_attempts=0, max_seconds=0.0, max_dim_candidates=4,
            seed_offsets=(0,), algorithms=("parallel",), configs=((10, 1e-3),),
            allow_dim_fallback=True,
        )
        exact = PVM.replace(discovery, allow_dim_fallback=False)
        with patch.object(PVM, "_fastica_safe", side_effect=factory):
            found = PVM._fit_ica_with_retries(X, 4, 42, "test", discovery)
            self.assertEqual(found["n_components"], 3)
            with self.assertRaises(RuntimeError):
                PVM._fit_ica_with_retries(X, 4, 42, "test", exact)

    def test_log_grid_covers_intermediate_and_upper_dimensions(self):
        base = {
            "n_pcs": 228,
            "explained_variance_ratio": np.ones(228, dtype=np.float32) / 228,
        }
        dims = PVM.propose_ica1_dims_from_pca_base(base, 0.9, target_count=10)
        self.assertEqual(dims[0], 2)
        self.assertEqual(dims[-1], 228)
        self.assertGreater(len(dims), 6)
        self.assertTrue(any(5 <= d <= 8 for d in dims))

    def test_axis_redundancy_detects_duplicate_extremes(self):
        rng = np.random.default_rng(7)
        x = rng.standard_t(df=3, size=400).astype(np.float32)
        S = np.column_stack([
            x,
            x + rng.normal(0, 0.01, size=len(x)),
            rng.normal(size=len(x)),
        ]).astype(np.float32)
        rows, _, _ = PVM.compute_axis_diagnostics(S, [S.copy()], [])
        self.assertLess(rows[0]["nonredundancy"], rows[2]["nonredundancy"])
        self.assertGreater(rows[0]["extreme_overlap"], 0.8)

    def test_csv_names_final_projection_cp_and_ica1_is_opt_in(self):
        df = pd.DataFrame({"id": [1, 2], "text": ["a", "b"]})
        Xf = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        Xi = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            PVM.export_run_csv(out, df, ["id", "text"], Xf, np.array([0, 1]), np.array([0.1, 0.2]), None)
            cols = pd.read_csv(out / "結果スコア.csv").columns.tolist()
            self.assertIn("CP1", cols)
            self.assertNotIn("IC1", cols)
            self.assertNotIn("ICA1_1", cols)
            PVM.export_run_csv(
                out, df, ["id", "text"], Xf, np.array([0, 1]), np.array([0.1, 0.2]), None,
                Xica1=Xi, include_ica1_cols=True,
            )
            cols = pd.read_csv(out / "結果スコア.csv").columns.tolist()
            self.assertIn("ICA1_3", cols)

    def test_cp_effective_requires_actual_reduction(self):
        rng = np.random.default_rng(3)
        Xi = rng.normal(size=(90, 6)).astype(np.float32)
        labels = np.repeat(np.arange(3), 30)
        Xi[labels == 1, 0] += 4
        Xi[labels == 2, 1] += 4
        _, _, rank, _ = PVM.between_class_projection(Xi, labels)
        self.assertLess(rank, Xi.shape[1])
        self.assertLessEqual(rank, 2)

    def test_last_resort_candidate_is_explicitly_degraded(self):
        X = np.eye(6, dtype=np.float32)
        fake = {
            "labels": np.array([0, 0, 0, 1, 1, 1]),
            "Xn": X,
            "bundle": SimpleNamespace(ica1_n_components=0, final_n_components=2),
            "transform_mode": "pca_pvm", "ica1_status": "failed", "ica2_status": "skipped",
            "fallback_level": 2, "retry_count": 0,
        }
        with patch.object(PVM, "get_pipeline_result", return_value=fake):
            rows = PVM._degraded_fallback_candidates(
                X, X, [4], 2, 2, 0.9, 42, {}, PVM.DEFAULT_ICA_RETRY,
                "fast", "forced fallback",
            )
        self.assertEqual(rows[0].selection_tier, "degraded")
        self.assertFalse(rows[0].quality_gate_passed)
        self.assertEqual(rows[0].degraded_reason, "forced fallback")

if __name__ == "__main__":
    unittest.main()

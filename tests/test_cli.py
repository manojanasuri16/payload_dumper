"""Tests for the CLI: listing (table + JSON), extraction, error paths."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from payload_dumper.cli import main


class TestListing:
    def test_json_list_to_stdout(self, multi_payload_file, capsys):
        rc = main([multi_payload_file, "-l", "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["block_size"] == 4096
        names = [p["name"] for p in data["partitions"]]
        assert names == ["boot", "system", "vendor"]
        # system has REPLACE_XZ + REPLACE_BZ + ZERO
        system = next(p for p in data["partitions"] if p["name"] == "system")
        assert set(system["types"]) >= {"REPLACE_XZ", "REPLACE_BZ", "ZERO"}
        assert system["operations"] == 3

    def test_table_list_prints_partitions(self, multi_payload_file, capsys):
        rc = main([multi_payload_file, "-l"])
        assert rc == 0
        out = capsys.readouterr().out
        # Rich output still contains the partition names as plain text
        assert "boot" in out
        assert "system" in out
        assert "vendor" in out


class TestExtract:
    def test_extracts_all_partitions(self, multi_payload_file, tmp_path):
        out_dir = tmp_path / "out"
        rc = main([multi_payload_file, "-o", str(out_dir), "-j", "1"])
        assert rc == 0
        for name in ("boot", "system", "vendor"):
            assert (out_dir / f"{name}.img").exists()

    def test_selective_extract(self, multi_payload_file, tmp_path):
        out_dir = tmp_path / "out"
        rc = main([multi_payload_file, "-o", str(out_dir), "-p", "boot", "-j", "1"])
        assert rc == 0
        assert (out_dir / "boot.img").exists()
        assert not (out_dir / "system.img").exists()

    def test_parallel_extract_matches_serial(self, multi_payload_file, tmp_path):
        """Running with -j 4 must produce the same bytes as -j 1."""
        serial_dir = tmp_path / "serial"
        parallel_dir = tmp_path / "parallel"
        assert main([multi_payload_file, "-o", str(serial_dir), "-j", "1"]) == 0
        assert main([multi_payload_file, "-o", str(parallel_dir), "-j", "4"]) == 0

        for name in ("boot", "system", "vendor"):
            a = (serial_dir / f"{name}.img").read_bytes()
            b = (parallel_dir / f"{name}.img").read_bytes()
            assert a == b, f"{name}.img differs between serial and parallel extraction"

    def test_zip_input_works(self, payload_zip_file, tmp_path, multi_partition_payload):
        """CLI should transparently drill into payload.bin inside the OTA zip."""
        out_dir = tmp_path / "out"
        rc = main([payload_zip_file, "-o", str(out_dir), "-j", "1"])
        assert rc == 0
        assert (out_dir / "boot.img").exists()
        assert (out_dir / "system.img").exists()
        assert (out_dir / "vendor.img").exists()

    def test_unknown_partition_warns_but_succeeds(self, multi_payload_file, tmp_path, capsys):
        out_dir = tmp_path / "out"
        rc = main([multi_payload_file, "-o", str(out_dir), "-p", "boot", "nope", "-j", "1"])
        assert rc == 0
        err = capsys.readouterr().out
        assert "nope" in err


class TestErrors:
    def test_missing_file_exits_nonzero(self, tmp_path):
        rc = main([str(tmp_path / "no-such-file.bin")])
        assert rc == 1

    def test_delta_payload_rejected(self, tmp_path, delta_payload):
        p = tmp_path / "delta.bin"
        p.write_bytes(delta_payload)
        rc = main([str(p), "-o", str(tmp_path / "out")])
        assert rc == 1

    def test_corrupt_magic_rejected(self, tmp_path, simple_payload_bytes):
        p = tmp_path / "bad.bin"
        p.write_bytes(b"XXXX" + simple_payload_bytes[4:])
        rc = main([str(p)])
        assert rc == 1

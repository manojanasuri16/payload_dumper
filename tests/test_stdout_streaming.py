"""Tests for `-o -` stdout streaming mode."""

from __future__ import annotations

import io

import pytest

from payload_dumper.cli import main
from payload_dumper.core import (
    PayloadError,
    extract_partition_to_stream,
    parse_payload,
)
from payload_dumper.source import FileSource


class TestExtractToStream:
    """Unit tests against extract_partition_to_stream — the building block."""

    def test_stream_output_matches_file_output(self, multi_payload_file, tmp_path):
        # file-based extract as reference
        from payload_dumper.core import extract_partition
        src = FileSource(multi_payload_file)
        try:
            payload = parse_payload(src)
            out_dir = tmp_path / "disk"
            out_dir.mkdir()
            part = next(p for p in payload.manifest.partitions if p.partition_name == "system")
            disk_path = extract_partition(src, payload.data_offset, part, str(out_dir))
            disk_bytes = open(disk_path, "rb").read()

            # stream into BytesIO, compare
            buf = io.BytesIO()
            extract_partition_to_stream(src, payload.data_offset, part, buf)
            assert buf.getvalue() == disk_bytes
        finally:
            src.close()

    def test_stream_progress_callback_fires_per_op(self, multi_payload_file):
        src = FileSource(multi_payload_file)
        try:
            payload = parse_payload(src)
            part = next(p for p in payload.manifest.partitions if p.partition_name == "system")
            calls: list[int] = []
            extract_partition_to_stream(
                src, payload.data_offset, part, io.BytesIO(), calls.append
            )
            assert sum(calls) == len(part.operations)
        finally:
            src.close()

    def test_stream_hash_mismatch_raises(self, multi_payload_file):
        src = FileSource(multi_payload_file)
        try:
            payload = parse_payload(src)
            part = payload.manifest.partitions[0]
            part.new_partition_info.hash = b"\x00" * 32
            with pytest.raises(PayloadError, match="final image hash mismatch"):
                extract_partition_to_stream(src, payload.data_offset, part, io.BytesIO())
        finally:
            src.close()


class TestCliStdout:
    """End-to-end tests for `payload-dumper -o -`."""

    def test_stdout_bytes_match_disk_extract(
        self, multi_payload_file, tmp_path, capsysbinary
    ):
        """Pure image bytes land on stdout; matches what `-o <dir>` writes."""
        # Reference: extract to disk
        out_dir = tmp_path / "ref"
        assert main([multi_payload_file, "-o", str(out_dir), "-p", "boot", "-j", "1"]) == 0
        reference = (out_dir / "boot.img").read_bytes()
        capsysbinary.readouterr()  # drain captured output from the disk run

        # Now stream the same partition to stdout
        rc = main([multi_payload_file, "-o", "-", "-p", "boot"])
        assert rc == 0
        captured = capsysbinary.readouterr()
        assert captured.out == reference

    def test_stdout_mode_keeps_banner_out_of_pipe(
        self, multi_payload_file, capsysbinary
    ):
        """Banner/progress must go to stderr so stdout stays clean."""
        rc = main([multi_payload_file, "-o", "-", "-p", "boot"])
        assert rc == 0
        captured = capsysbinary.readouterr()
        # image bytes should start with whatever `boot.img` starts with —
        # definitely not the string "source" / "payload" from the banner
        assert b"source" not in captured.out[:200]
        assert b"payload" not in captured.out[:200]
        # banner did land somewhere — on stderr
        assert b"source" in captured.err or b"payload" in captured.err

    def test_stdout_without_partition_flag_errors(self, multi_payload_file, capsys):
        rc = main([multi_payload_file, "-o", "-"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "exactly one partition" in err

    def test_stdout_with_multiple_partitions_errors(self, multi_payload_file, capsys):
        rc = main([multi_payload_file, "-o", "-", "-p", "boot", "system"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "exactly one partition" in err

    def test_stdout_with_unknown_partition_errors(self, multi_payload_file, capsys):
        """Warning filters the selection down to 0; we should not silently succeed."""
        rc = main([multi_payload_file, "-o", "-", "-p", "nope"])
        # 0 partitions matched — falls out before reaching the stdout path
        assert rc == 0  # matches existing "no partitions to extract" behavior
        out = capsys.readouterr()
        assert "nope" in (out.err + out.out)

    def test_stdout_mode_still_validates_delta_payload(
        self, tmp_path, delta_payload, capsys
    ):
        p = tmp_path / "delta.bin"
        p.write_bytes(delta_payload)
        rc = main([str(p), "-o", "-", "-p", "system"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "SOURCE_COPY" in err

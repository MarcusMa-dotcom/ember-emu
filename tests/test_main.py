"""Tests of the command line."""

import pytest

from emberemu_app.__main__ import build_parser, main


def test_nothing_to_run_is_refused(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--no-gui", "--no-web"])
    assert stopped.value.code == 2
    assert "leave nothing to run" in capsys.readouterr().err


def test_tls_options_belong_together(capsys):
    with pytest.raises(SystemExit):
        main(["--no-gui", "--tls-cert", "cert.pem"])
    assert "belong together" in capsys.readouterr().err


def test_options():
    args = build_parser().parse_args(["--no-gui", "--no-web", "--serve", "--port", "8090"])
    assert args.serve and args.no_gui and args.no_web and args.port == 8090
    assert build_parser().parse_args([]).port == 8089

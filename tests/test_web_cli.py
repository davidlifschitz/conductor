"""CLI parser tests for the web dashboard subcommand."""

from __future__ import annotations

import pytest

from conductor.dashboard.__main__ import build_parser

EXPECTED_WEB_HOST = "127.0.0.1"
EXPECTED_WEB_PORT = 8485


@pytest.fixture
def parser():
    return build_parser()


def test_web_subcommand_defaults(parser):
    args = parser.parse_args(["web"])
    assert args.cmd == "web"
    assert args.host == EXPECTED_WEB_HOST
    assert args.port == EXPECTED_WEB_PORT


def test_web_flag_on_default_live(parser):
    args = parser.parse_args(["--web"])
    assert args.cmd == "live"
    assert args.web is True
    assert args.host == EXPECTED_WEB_HOST
    assert args.port == EXPECTED_WEB_PORT


def test_web_subcommand_custom_port(parser):
    args = parser.parse_args(["web", "--port", "9000"])
    assert args.cmd == "web"
    assert args.port == 9000
    assert args.host == EXPECTED_WEB_HOST
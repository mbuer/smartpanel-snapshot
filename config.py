#!/usr/bin/env python3

"""
Shared configuration loader for SmartPanel Snapshot.
"""

from pathlib import Path

import yaml


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "config.yaml"


def load_config():
    """
    Load and validate config.yaml.
    """

    if not CONFIG_FILE.is_file():
        raise FileNotFoundError(
            f"Configuration file not found: {CONFIG_FILE}"
        )

    with CONFIG_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ValueError(
            "config.yaml must contain a YAML mapping."
        )

    required_sections = {
        "smartpanels",
        "paths",
        "logging",
        "api",
    }

    missing_sections = (
        required_sections - config.keys()
    )

    if missing_sections:
        raise ValueError(
            "config.yaml is missing required section(s): "
            + ", ".join(sorted(missing_sections))
        )

    return config


def save_config(config):
    """
    Write configuration back to config.yaml.
    """

    with CONFIG_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        yaml.safe_dump(
            config,
            file,
            sort_keys=False,
        )


# Initial configuration used by modules that load settings at startup.
CONFIG = load_config()

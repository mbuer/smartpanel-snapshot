#!/usr/bin/env python3

"""
Shared configuration loader for SmartPanel Snapshot.

Loads project settings from config.yaml and provides them to the
application modules from one central location.

The configuration file is resolved relative to this source file so
the project can be cloned or moved without relying on the shell's
current working directory.
"""

from pathlib import Path

import yaml


# Project root directory.
PROJECT_DIR = Path(__file__).resolve().parent

# Main YAML configuration file.
CONFIG_FILE = PROJECT_DIR / "config.yaml"


def load_config():
    """
    Load and validate config.yaml.

    Returns:
        Dictionary containing the parsed configuration.

    Raises:
        FileNotFoundError:
            config.yaml does not exist.

        ValueError:
            Configuration is empty or missing required sections.

        yaml.YAMLError:
            YAML syntax is invalid.
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


# Load configuration once when this module is imported.
CONFIG = load_config()

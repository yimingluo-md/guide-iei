#!/usr/bin/env python3
"""Source-neutral entry point for clinical protein residue/change matching.

The implementation remains import-compatible with the historical
``clinvar_aa_match`` module because downstream users and older tests import its
public helpers directly. New pipeline integrations should invoke this module.
"""

from __future__ import annotations

try:
    from .clinvar_aa_match import *  # noqa: F401,F403 - compatibility facade
    from .clinvar_aa_match import main
except ImportError:  # direct script execution
    from clinvar_aa_match import *  # noqa: F401,F403 - compatibility facade
    from clinvar_aa_match import main


if __name__ == "__main__":
    raise SystemExit(main())

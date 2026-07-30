"""Filter spec documentation files for VP or QS deployment modes.

Marker syntax (HTML comments, invisible in GitHub renders):
    <!-- vp-only -->   start of a VP-only section
    <!-- qs-only -->   start of a QS-only section
    <!-- end -->       end of the conditional section

Sections with no marker appear in both outputs unchanged.
Marker comment lines themselves are stripped from all output.

Example:
    ## Installation

    <!-- vp-only -->
    ### Option A: Validated Pattern
    ./vp-out/pattern.sh make install
    <!-- end -->

    <!-- qs-only -->
    ### Option B: Quickstart
    make keycloak && make openshell-saw-create
    <!-- end -->

    ## Architecture
    This section appears in both VP and QS outputs.
"""

import re
from pathlib import Path

_VP_ONLY  = re.compile(r'^\s*<!--\s*vp-only\s*-->\s*$', re.IGNORECASE)
_QS_ONLY  = re.compile(r'^\s*<!--\s*qs-only\s*-->\s*$', re.IGNORECASE)
_END      = re.compile(r'^\s*<!--\s*end\s*-->\s*$',     re.IGNORECASE)


def filter_doc(content: str, deploy_mode: str) -> str:
    """Return content with sections not applicable to deploy_mode removed.

    deploy_mode: 'vp' or 'qs'
    Marker lines are always stripped from output regardless of mode.
    """
    if deploy_mode not in ('vp', 'qs'):
        raise ValueError(f"deploy_mode must be 'vp' or 'qs', got {deploy_mode!r}")

    lines = content.split('\n')
    result = []
    skip = False

    for line in lines:
        if _VP_ONLY.match(line):
            skip = (deploy_mode == 'qs')   # skip vp-only content when generating QS
        elif _QS_ONLY.match(line):
            skip = (deploy_mode == 'vp')   # skip qs-only content when generating VP
        elif _END.match(line):
            skip = False
        elif not skip:
            result.append(line)

    return '\n'.join(result)


def process_docs(doc_entries, spec_dir: str, output_dir: Path, deploy_mode: str):
    """Read, filter, and write each DocEntry for the given deploy_mode.

    doc_entries: list of DocEntry objects (from ApplicationSpec.docs)
    spec_dir:    directory containing spec.yaml (source files are relative to this)
    output_dir:  vp-out/ or qs-out/ Path
    deploy_mode: 'vp' or 'qs'
    """
    if not doc_entries or not spec_dir:
        return

    spec_path = Path(spec_dir)

    for entry in doc_entries:
        # Skip files scoped to the other deploy mode
        if entry.deploy not in ('both', deploy_mode):
            continue

        source_path = spec_path / entry.source
        if not source_path.exists():
            # Warn but don't fail — missing source is a spec authoring error,
            # not a compiler error; the rest of generation should still succeed.
            import warnings
            warnings.warn(f"docs source not found: {source_path}", stacklevel=2)
            continue

        content = source_path.read_text(encoding='utf-8')
        filtered = filter_doc(content, deploy_mode)

        target_path = output_dir / entry.target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(filtered, encoding='utf-8')

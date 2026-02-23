from applib.config import config
from applib.helpers import load_json
from applib.models.code import CodeGuidance
from applib.models.claim import Adjustment, Claim
from collections import defaultdict
from pathlib import Path

_CODE_GUIDANCE_FOLDER: Path = config.APPDATA_FOLDER_PATH / "code_guidance"

def _load_code_guidance() -> dict[str, dict[str, CodeGuidance]]:
    d = defaultdict(dict)
    for file in _CODE_GUIDANCE_FOLDER.glob("*.json"):
        j = load_json(file)
        for group_code, group_map in j.items():
            for reason_code, reason_map in group_map['reason_codes'].items():
                gc = group_code.upper()
                rc = reason_code.upper()

                d[gc][rc] = CodeGuidance(
                    group_code=gc,
                    reason_code=rc,
                    **reason_map
                )
    return d

def get_code_guidance(group_code: str = "", reason_code: str = "") -> CodeGuidance:
    gc = group_code.upper()
    rc = reason_code.upper()
    return (
        GUIDANCE_MAP[gc]
        .get(
            rc,
            GUIDANCE_MAP['CARC'].get(rc, CodeGuidance())
        )
    )

GUIDANCE_MAP: dict[str, dict[str, CodeGuidance]] = _load_code_guidance()

def add_guidance_to_adjustment(adjustment: Adjustment) -> None:
    """Adds guidance to an adjustment object in-place"""
    guidance: CodeGuidance = get_code_guidance(group_code=adjustment.group_code, reason_code=adjustment.reason_code)

    if guidance:
        adjustment.guidance = guidance


def add_guidance_to_claim_adjustments(claim: Claim) -> None:
    """Adds guidance to a claim object in-place"""
    for claim_835 in claim.edi_mappings:
        for service in claim_835.services:
            for adjustment in service.adjustments:
                add_guidance_to_adjustment(adjustment)

    for adjustment in claim.adjustments:
        add_guidance_to_adjustment(adjustment)

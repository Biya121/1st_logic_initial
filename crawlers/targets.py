"""PDF 기준 8개 품목 타깃 (HSA CSV licence_no는 실제 등재 기준)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetProduct:
    key: str
    trade_name: str
    product_id: str
    licence_no: str | None
    market_segment: str
    notes: str


TARGETS: tuple[TargetProduct, ...] = (
    TargetProduct(
        key="hydrine",
        trade_name="Hydrine",
        product_id="SG_hydrine_hydroxyurea_500",
        licence_no="SIN11083P",
        market_segment="tender",
        notes="HYDRINE CAPSULES 500 mg",
    ),
    TargetProduct(
        key="gadvoa",
        trade_name="Gadvoa Inj.",
        product_id="SG_gadvoa_gadobutrol_604",
        licence_no="SIN12399P",
        market_segment="tender",
        notes="GADOVIST 레퍼런스",
    ),
    TargetProduct(
        key="sereterol",
        trade_name="Sereterol Activair",
        product_id="SG_sereterol_activair",
        licence_no="SIN11529P",
        market_segment="retail",
        notes="SERETIDE EVOHALER 25/50",
    ),
    TargetProduct(
        key="omethyl",
        trade_name="Omethyl",
        product_id="SG_omethyl_omega3_2g",
        licence_no="SIN14504P",
        market_segment="retail",
        notes="Omacor 1000mg 대응 ethyl esters",
    ),
    TargetProduct(
        key="rosumeg",
        trade_name="Rosumeg Combigel",
        product_id="SG_rosumeg_combigel",
        licence_no=None,
        market_segment="combo_drug",
        notes="타입 B 복합제",
    ),
    TargetProduct(
        key="atmeg",
        trade_name="Atmeg Combigel",
        product_id="SG_atmeg_combigel",
        licence_no=None,
        market_segment="combo_drug",
        notes="타입 B 복합제",
    ),
    TargetProduct(
        key="ciloduo",
        trade_name="Ciloduo",
        product_id="SG_ciloduo_cilosta_rosuva",
        licence_no=None,
        market_segment="wholesale",
        notes="타입 C SAR",
    ),
    TargetProduct(
        key="gastiin",
        trade_name="Gastiin CR",
        product_id="SG_gastiin_cr_mosapride",
        licence_no=None,
        market_segment="wholesale",
        notes="타입 C SAR",
    ),
)

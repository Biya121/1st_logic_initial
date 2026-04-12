"""싱가포르 브랜드 → INN 등록 (PDF §4)."""

from __future__ import annotations

from inn_normalizer import _inn


def register_sg_brands() -> None:
    _inn.register_brand("Hydrine", "hydroxyurea")
    _inn.register_brand("Gadvoa", "gadobutrol")
    _inn.register_brand("Gadvoa Inj.", "gadobutrol")
    _inn.register_brand("Sereterol", "fluticasone/salmeterol")
    _inn.register_brand("Sereterol Activair", "fluticasone/salmeterol")
    _inn.register_brand("Omethyl", "omega-3 acid ethyl esters")
    _inn.register_brand("Cutielet", "omega-3 acid ethyl esters")
    _inn.register_brand("Rosumeg", "rosuvastatin/omega-3 acid ethyl esters")
    _inn.register_brand("Rosumeg Combigel", "rosuvastatin/omega-3 acid ethyl esters")
    _inn.register_brand("Atmeg", "atorvastatin/omega-3 acid ethyl esters")
    _inn.register_brand("Atmeg Combigel", "atorvastatin/omega-3 acid ethyl esters")
    _inn.register_brand("Ciloduo", "cilostazol/rosuvastatin")
    _inn.register_brand("Gastiin", "mosapride")
    _inn.register_brand("Gastiin CR", "mosapride")

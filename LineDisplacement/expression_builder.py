# -*- coding: utf-8 -*-
# expression_builder.py – erzeugt den QGIS-Geometriegenerator-Ausdruck
# Zweisprachige Kommentartexte via t("DE","EN")

from .i18n import t


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _norm_layer_spec(spec) -> dict:
    if isinstance(spec, str):
        return {
            'name': spec,
            'simplify': None,
            'smooth_enabled': False,     # Default
            'smooth_offset': None,
            'smooth_iter': None,
            'crs_authid': None
        }
    if isinstance(spec, dict) and 'name' in spec:
        return {
            'name': spec['name'],
            'simplify': spec.get('simplify'),
            'smooth_enabled': bool(spec.get('smooth_enabled', False)),
            'smooth_offset': spec.get('smooth_offset'),
            'smooth_iter': spec.get('smooth_iter'),
            'crs_authid': spec.get('crs_authid'),
        }
    raise ValueError(t("Layer-Spezifikation muss String oder Dict mit 'name' sein.",
                       "Layer specification must be a string or a dict with 'name'."))


def _escape(name: str) -> str:
    return name.replace("'", "''")


def _per_feature_pipeline(src_authid: str | None, target_authid: str | None,
                          simplify, smooth_enabled, smooth_offset, smooth_iter) -> str:
    """
    Baut die pro-Feature-Teilpipeline als Ausdrucksfragment:
      transform? -> simplify? -> smooth?
    Ergebnis ist ein Ausdruck, der $geometry ersetzt.
    """
    term = "$geometry"
    if src_authid and target_authid and src_authid != target_authid:
        term = f"transform({term}, '{src_authid}', '{target_authid}')"

    if isinstance(simplify, (int, float)) and simplify > 0:
        term = f"simplify({term}, {simplify})"

    if smooth_enabled:
        # Wenn beide 0 oder None -> ohne Parameter (QGIS-Standardwerte)
        use_defaults = (smooth_offset in (None, 0, 0.0)) and (smooth_iter in (None, 0))
        if use_defaults:
            term = f"smooth({term})"
        else:
            off = (0.0 if smooth_offset in (None,) else smooth_offset)
            it  = (1   if smooth_iter  in (None,) else smooth_iter)
            term = f"smooth({term}, {off}, {it})"

    return term


def _aggregate_layer(layer: dict, target_authid: str | None) -> str:
    """
    aggregate('<layer>', 'collect', <pro-Feature-Pipeline>).
    layer: {
      'name', 'crs_authid',
      'simplify',
      'smooth_enabled', 'smooth_offset', 'smooth_iter'
    }
    """
    name = _escape(layer['name'])
    term = _per_feature_pipeline(
        src_authid     = layer.get('crs_authid'),
        target_authid  = target_authid,
        simplify       = layer.get('simplify'),
        smooth_enabled = layer.get('smooth_enabled', False),
        smooth_offset  = layer.get('smooth_offset'),
        smooth_iter    = layer.get('smooth_iter'),
    )
    return f"aggregate('{name}', 'collect', {term})"


def _union_nested(terms: list[str]) -> str:
    """
    Baut eine verschachtelte union(...) mit Zeilenumbrüchen für bessere Lesbarkeit.
    """
    if not terms:
        return "geometry(NULL)"
    if len(terms) == 1:
        return terms[0]
    expr = terms[0]
    for t_term in terms[1:]:
        expr = f"union(\n  {expr},\n  {t_term}\n)"
    return expr


def _norm_params(params, default_iters: int) -> list:
    # [ Toleranz ggü. Löchern, max. Winkelabweichung, vereinfachende Betrachtung,
    #   max. Iterationen (0 = aus), an Kreuzungen erstmal zerlegen, an Kreuzungen nicht verknüpfen ]
    defaults = [0.01, 90.0, 0.3, default_iters, True, False]
    if not isinstance(params, (list, tuple)) or len(params) == 0:
        return defaults
    out = list(defaults)
    for i in range(min(6, len(params))):
        out[i] = params[i]
    return out


def _arr_literal(vals: list) -> str:
    def lit(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)
    return "array(" + ", ".join(lit(x) for x in vals) + ")"


def _arr_literal_with_comments(vals: list) -> str:
    """
    Gibt ein array(...) mit je einem Wert pro Zeile und einem erklärenden Kommentar aus.
    Erwartete Reihenfolge:
      0: Toleranz ggü. Löchern
      1: max. Winkelabweichung
      2: vereinfachende Betrachtung
      3: max. Iterationen (0 = aus)
      4: an Kreuzungen erstmal zerlegen
      5: an Kreuzungen nicht verknüpfen
    """
    labels = [
        t("Toleranz ggü. Löchern", "Tolerance for holes"),
        t("max. Winkelabweichung", "Max. angle deviation"),
        t("vereinfachende Betrachtung", "Simplified assessment"),
        t("max. Iterationen (0 = aus)", "Max. iterations (0 = off)"),
        t("an Kreuzungen erstmal zerlegen", "Initially split at intersections"),
        t("an Kreuzungen nicht verknüpfen", "Do not connect at intersections"),
    ]

    def lit(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    lines = ["array("]
    for i, v in enumerate(vals):
        comma = "," if i < len(vals) - 1 else ""
        # Kommentar nach dem Wert (QGIS-Ausdruckskommentar mit --)
        lines.append(f"  {lit(v)}{comma}\t-- {labels[i]}")
    lines.append(")")
    return "\n".join(lines)


def build_line_displacement_call(
    to_move_layers,
    fixed_layers,
    target_layer_name: str,
    buf_dist,
    min_repl_len,
    pre_params,
    fin_params,
    debug_stage: str,
    log_to_desktop: bool = False,
    target_authid: str | None = None
) -> str:
    # --- zu verdrängende Geometrie: pro Feature -> collect, ggf. mehrere Layer -> union ---
    move_terms = [_aggregate_layer(L, target_authid) for L in to_move_layers]
    move_expr  = _union_nested(move_terms)

    # --- bleibende Geometrie: identisches Schema ---
    fixed_terms = [_aggregate_layer(L, target_authid) for L in fixed_layers]
    fixed_expr  = _union_nested(fixed_terms)

    # Parameterblöcke (6 Elemente) normalisieren
    pre6 = _norm_params(pre_params, default_iters=0)
    fin6 = _norm_params(fin_params, default_iters=0)

    # Ziel, Zahlen, Flags
    tgt    = _q(target_layer_name)
    minlen = 0 if (min_repl_len is None) else min_repl_len
    logfl  = "true" if log_to_desktop else "false"

    # Debug-Stufen – gewünschte als aktive Zeile, alle übrigen auskommentiert
    debug_options = [
        "final",
        "union_to_move",
        "union_fixed",
        "fixed_buffer_poly",
        "fixed_boundary",
        "to_move_in_buffer",
        "loops",
        "loops_union",
        "loops_buffer",
        "buffer_blobs",
        "used_blobs",
        "unused_blobs",
        "boundary_segments",
        "segments_to_move",
        "replacement_segments",
        "rejected_replacements",
        "crossers",
        "rest",
        "pre_final",
    ]
    if not debug_stage:
        debug_stage = "final"

    dbg_lines = []
    for opt in debug_options:
        if opt == debug_stage:
            dbg_lines.append(f"'{opt}',")
        else:
            dbg_lines.append(f"--'{opt}',")

    # Ausdruck mit genau den gewünschten Kommentaren zusammenbauen
    parts = []

    # Kopf mit Bedienhinweisen
    parts.append(t("-- Zum Ausführen bitte", "-- To execute, please"))
    parts.append(t("-- 1. Symbolebene einschalten,", "-- 1. enable the symbol layer,"))
    parts.append(t("-- 2. Ebene unsichtbar schalten,", "-- 2. hide the layer,"))
    parts.append(t("-- 3. Ebene bearbeitbar schalten,", "-- 3. set the layer editable,"))
    parts.append(t("-- 4. Ebene wieder sichtbar schalten,", "-- 4. show the layer again,"))
    parts.append(t("-- 5. warten, bis Geometrie generiert wurde, und", "-- 5. wait until geometry is generated and"))
    parts.append(t("-- 6. Bearbeitbarkeit wieder abschalten.", "-- 6. disable edit mode again."))
    parts.append("")
    parts.append("line_displacement(")

    # 1) zu verdrängende Geometrie
    parts.append(t("-- 1) zu verdrängende Geometrie:", "-- 1) Geometry to be displaced:"))
    parts.append(move_expr + ",")
    parts.append("")

    # 2) bleibende Geometrie
    parts.append(t("-- 2) bleibende Geometrie:", "-- 2) Fixed geometry:"))
    parts.append(fixed_expr + ",")
    parts.append("")

    # 3) Ziel-Ebene
    parts.append(t("-- 3) Ziel-Ebene:", "-- 3) Target layer:"))
    parts.append(tgt + ",")
    parts.append("")

    # 4) Verdrängungs-Abstand
    parts.append(t("-- 4) Verdrängungs-Abstand:", "-- 4) Displacement distance:"))
    parts.append(f"{buf_dist},")
    parts.append("")

    # 5) Mindestlänge verdrängter Strecken
    parts.append(t("-- 5) Mindestlänge verdrängter Strecken:", "-- 5) Minimum length of displaced segments:"))
    parts.append(f"{minlen},")
    parts.append("")

    # 6) Fragmente verknüpfen vorher
    parts.append(t("-- 6) Fragmente verknüpfen vorher:", "-- 6) Connect fragments before:"))
    parts.append(_arr_literal_with_comments(pre6) + ",")
    parts.append("")

    # 7) Fragmente verknüpfen nachher
    parts.append(t("-- 7) Fragmente verknüpfen nachher:", "-- 7) Connect fragments after:"))
    parts.append(_arr_literal_with_comments(fin6) + ",")
    parts.append("")

    # 8) Debug-Stufe
    parts.append(t("-- 8) Debug-Stufe:", "-- 8) Debug stage:"))
    parts.extend(dbg_lines)
    parts.append("")

    # 9) Logdatei auf Desktop
    parts.append(t("-- 9) Logdatei auf Desktop:", "-- 9) Write log file on desktop:"))
    parts.append(logfl)
    parts.append(")")

    return "\n".join(parts)

"""La nota de un sub-loop: su validación y lo que ve el orquestador (spec del orquestador §4.3, RF-91).

El sub-loop la entrega con `entregar_nota`. El backend controla que cada SKU haya salido de una búsqueda y que cada
renglón del encargo quede en un solo lugar. Rechaza hasta `MAX_RECHAZOS` veces; después la completa él.
"""

from typing import Any

from internal.schemas.agent_schema import EntregarNotaInput
from internal.services import inventory_service

MAX_RECHAZOS = 2
PREGUNTA_SIN_CUENTA = "¿me confirmás qué querías en este renglón?"
TIPO = "sub_loop"


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


def validar(data: EntregarNotaInput, encargo: list[int], vistos: set[str]) -> dict[str, Any] | None:
    """El error que vuelve al sub-loop, o None si la nota es válida (CA-154, CA-155)."""
    skus = [r.sku for r in data.resueltos] + [sku for p in data.pendientes for sku in p.opciones]
    if inventados := sorted({sku for sku in skus if sku not in vistos}):
        return _error(
            "sku_no_buscado",
            f"Estos SKU no salieron de ninguna búsqueda: {', '.join(inventados)}. Buscalos y usá el SKU que devuelve "
            "la tool, o dejá el renglón en `pendientes`",
            skus=inventados,
        )
    lugares: dict[int, set[str]] = {}
    for lugar, items in (("resueltos", data.resueltos), ("pendientes", data.pendientes), ("afuera", data.afuera)):
        for item in items:
            lugares.setdefault(item.renglon, set()).add(lugar)
    if ajenos := sorted(n for n in lugares if n not in encargo):
        return _error(
            "renglon_fuera_del_encargo",
            f"Los renglones {ajenos} no son de tu encargo ({encargo}): sacalos de la nota",
            renglones=ajenos,
        )
    if repetidos := sorted(n for n, donde in lugares.items() if len(donde) > 1):
        return _error(
            "renglon_repetido",
            f"Los renglones {repetidos} están en más de un lugar: cada uno va solo en `resueltos`, `pendientes` o "
            "`afuera`",
            renglones=repetidos,
        )
    if sin_cuenta := [n for n in encargo if n not in lugares]:
        return _error(
            "renglones_sin_cuenta",
            f"Faltan los renglones {sin_cuenta}: cada renglón del encargo va en `resueltos`, en `pendientes` con la "
            "pregunta o en `afuera` con el motivo. Volvé a entregar la nota con todos",
            renglones=sin_cuenta,
        )
    return None


def completar(data: EntregarNotaInput, encargo: list[int], vistos: set[str]) -> dict[str, Any]:
    """La nota después del último rechazo: sin lo inválido, y lo que queda sin cuenta va a `pendientes` (RF-91)."""
    # Un renglón puede resolverse con varias líneas (dos tonos): todas van, si cada SKU salió de una búsqueda
    resueltos = [
        item.model_dump(exclude_none=True)
        for item in data.resueltos
        if item.renglon in encargo and item.sku in vistos
    ]
    ubicados = {resuelto["renglon"] for resuelto in resueltos}
    pendientes: list[dict[str, Any]] = []
    afuera: list[dict[str, Any]] = []
    for item in data.pendientes:
        if item.renglon in encargo and item.renglon not in ubicados:
            pendientes.append({**item.model_dump(), "opciones": [sku for sku in item.opciones if sku in vistos]})
            ubicados.add(item.renglon)
    for item in data.afuera:
        if item.renglon in encargo and item.renglon not in ubicados:
            afuera.append(item.model_dump())
            ubicados.add(item.renglon)
    sin_cuenta = [n for n in encargo if n not in ubicados]
    pendientes += [{"renglon": n, "pregunta": PREGUNTA_SIN_CUENTA, "opciones": []} for n in sin_cuenta]
    return {"resueltos": resueltos, "pendientes": pendientes, "afuera": afuera, "observaciones": data.observaciones}


def con_lo_encontrado(encargo: list[int], busquedas: dict[int, tuple[dict, dict]]) -> dict[str, Any]:
    """La nota que arma el backend si el sub-loop llega a su tope sin entregarla (RF-93, CA-156).

    Usa el último resultado de `buscar_renglones` de cada renglón (de la precarga o del sub-loop): `unico` va
    resuelto, lo que no existe o no tiene stock va afuera y el resto queda para preguntar.
    """
    nota: dict[str, Any] = {"resueltos": [], "pendientes": [], "afuera": [], "observaciones": ""}
    for n in encargo:
        if n not in busquedas:
            nota["pendientes"].append({"renglon": n, "pregunta": PREGUNTA_SIN_CUENTA, "opciones": []})
            continue
        buscado, resultado = busquedas[n]
        candidatos = [candidato["sku"] for candidato in resultado["candidatos"]]
        estado = resultado["estado"]
        if estado == "unico":
            resuelto = {"renglon": n, "sku": candidatos[0], "cantidad": buscado.get("cantidad", 1)}
            nota["resueltos"].append({**resuelto, **({"tono": buscado["tono"]} if buscado.get("tono") else {})})
        elif estado == "no_encontrado":
            nota["afuera"].append({"renglon": n, "motivo": "fuera_de_catalogo"})
        elif estado == "sin_stock" and not resultado.get("falta", {}).get("available"):
            nota["afuera"].append({"renglon": n, "motivo": "sin_stock"})
        else:
            pregunta = f"¿cuál de estos? ({', '.join(resultado.get('difieren_en') or [])})".replace(" ()", "")
            nota["pendientes"].append({"renglon": n, "pregunta": pregunta, "opciones": candidatos})
    nota["observaciones"] = "El sub-loop llegó a su tope: la nota la armó el sistema con lo que encontró."
    return nota


def _nombres(skus: set[str]) -> dict[str, str]:
    try:
        catalogo = {product.sku: product for product in inventory_service.get_catalog()}
    except OSError:
        return {}
    return {
        sku: " ".join(filter(None, (catalogo[sku].name, catalogo[sku].presentation)))
        for sku in skus
        if sku in catalogo
    }


def para_orquestador(entrada: dict[str, Any]) -> dict[str, Any]:
    """Lo que vuelve como resultado de `delegar_skill`: la nota con el nombre de cada producto, sin los intentos."""
    nota = entrada["nota"]
    skus = {r["sku"] for r in nota["resueltos"]} | {sku for p in nota["pendientes"] for sku in p["opciones"]}
    return {
        "skill": entrada["skill"],
        **nota,
        "productos": _nombres(skus),  # para nombrarlos en el mensaje; los SKU no van al cliente
        # SKU → unidades que quedan, de los que no alcanzaron: el único caso en que el número va al cliente (BR-01)
        "disponibles": dict(entrada.get("faltantes") or {}),
        "completa": entrada["cerrada_por"] == "nota",
    }


def skus(entrada: dict[str, Any]) -> set[str]:
    """Los SKU de una nota anterior: cuentan como buscados si se vuelven a delegar esos renglones (RF-97)."""
    nota = entrada["nota"]
    return {r["sku"] for r in nota["resueltos"]} | {sku for p in nota["pendientes"] for sku in p["opciones"]}

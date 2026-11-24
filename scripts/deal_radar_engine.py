#!/usr/bin/env python3
"""
Smart Deal & Price Radar - Motor anti falsas rebajas.

Lee:
  data/price_history.json  -> productos reales con el historial de precios de cada tienda
  data/stores.json         -> tiendas y los criterios de fiabilidad comprobados

Genera:
  data/deals.json   -> ofertas listas para la web (ordenadas por mayor ahorro real)
  data/alerts.json  -> chollos fuertes para Telegram (bajada real >= 50 %)
  data/status.json  -> estado del motor (para el panel de administración)

Reglas:
  * Solo librería estándar de Python.
  * Nunca inventa datos: si un producto no tiene historial suficiente,
    no se calcula ningún porcentaje de ahorro.
  * Solo se muestran ofertas de tiendas que cumplen TODOS los criterios
    de fiabilidad y con precio actualizado hace menos de 48 horas.
  * El ahorro se mide contra la media real de los últimos 90 días,
    no contra el precio tachado de la tienda.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

MAX_PRICE_AGE_HOURS = 48     # precio más viejo que esto = no se muestra
MIN_HISTORY_DAYS = 30        # días mínimos de historial para calcular ahorro
MIN_HISTORY_POINTS = 10      # puntos mínimos de historial
WINDOW_DAYS = 90             # ventana de la media
ALERT_MIN_SAVING = 50        # % mínimo para alerta de Telegram

STORE_CHECKS = [
    "legal_company_data",    # empresa con datos legales visibles
    "real_reviews",          # opiniones reales de clientes con buena nota
    "secure_payment",        # pago protegido (tarjeta / PayPal)
    "returns_14_days",       # devoluciones en al menos 14 días
    "real_shipping_times",   # plazos de envío reales publicados
]

MENUS = {
    "moda": ["mujer", "hombre", "calzado-complementos", "outlets-ventas-privadas"],
    "tecnologia": ["moviles-tablets", "ordenadores-componentes", "gaming-consolas",
                   "tv-audio", "perifericos"],
    "supermercado": ["despensa", "bebidas", "proteinas-saludable", "packs-ahorro",
                     "liquidaciones"],
    "belleza": ["cremas-serums", "alternativas", "tratamientos-clinicas"],
    "viajes": ["vuelos", "cuando-volar", "ofertas-vuelos", "vuelo-hotel", "hoteles",
               "apartamentos", "entradas-atracciones"],
}

UNIT_LABEL = {"kg": "€/kg", "l": "€/L", "ml": "€/ml", "ud": "€/ud"}


def now_utc():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None
    try:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def load_json(name, default):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(name, payload):
    path = os.path.join(DATA, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def evaluate_stores(stores_raw):
    """Una tienda está verificada solo si cumple TODOS los criterios
    y cada criterio tiene una fuente (enlace) que lo demuestra."""
    result = {}
    for st in stores_raw.get("stores", []):
        checks = st.get("checks", {})
        passed = []
        failed = []
        for key in STORE_CHECKS:
            item = checks.get(key) or {}
            ok = bool(item.get("ok")) and bool(item.get("source"))
            (passed if ok else failed).append(key)
        result[st.get("id")] = {
            "id": st.get("id"),
            "name": st.get("name", st.get("id")),
            "type": st.get("type", "tienda"),
            "ships_from": st.get("ships_from", ""),
            "verified": not failed,
            "passed": passed,
            "failed": failed,
            "checks": checks,
            "checked_on": st.get("checked_on", ""),
        }
    return result


def history_stats(history, ref_time):
    """Media, mínimo y número de días del historial en la ventana de 90 días."""
    start = ref_time - timedelta(days=WINDOW_DAYS)
    points = []
    for h in history or []:
        dt = parse_dt(h.get("date"))
        price = h.get("price")
        if dt is None or not isinstance(price, (int, float)) or price <= 0:
            continue
        if start <= dt <= ref_time:
            points.append((dt, float(price)))
    if not points:
        return None
    points.sort()
    days = (points[-1][0] - points[0][0]).days
    prices = [p for _, p in points]
    return {
        "avg90": round(sum(prices) / len(prices), 2),
        "min90": round(min(prices), 2),
        "points": len(prices),
        "days": days,
        "enough": days >= MIN_HISTORY_DAYS and len(prices) >= MIN_HISTORY_POINTS,
    }


def unit_price(price, unit):
    if not unit:
        return None
    kind = unit.get("type")
    amount = unit.get("amount")
    if kind not in UNIT_LABEL or not isinstance(amount, (int, float)) or amount <= 0:
        return None
    return {"value": round(price / amount, 4 if kind == "ml" else 2),
            "label": UNIT_LABEL[kind]}


def drop_band(saving):
    if saving is None:
        return None
    if saving >= 70:
        return 70
    if saving >= 60:
        return 60
    if saving >= 50:
        return 50
    return None


def process(products_raw, stores, ref_time):
    deals = []
    skipped = {"store_not_verified": 0, "price_too_old": 0,
               "no_valid_offer": 0, "bad_menu": 0}

    for prod in products_raw.get("products", []):
        menu = prod.get("menu")
        section = prod.get("section")
        if menu not in MENUS or section not in MENUS[menu]:
            skipped["bad_menu"] += 1
            continue

        offers = []
        for off in prod.get("offers", []):
            store = stores.get(off.get("store"))
            price = off.get("price")
            updated = parse_dt(off.get("updated"))
            if not isinstance(price, (int, float)) or price <= 0 or not off.get("url"):
                continue
            if store is None or not store["verified"]:
                skipped["store_not_verified"] += 1
                continue
            if updated is None or ref_time - updated > timedelta(hours=MAX_PRICE_AGE_HOURS):
                skipped["price_too_old"] += 1
                continue
            stats = history_stats(off.get("history"), ref_time)
            saving = None
            if stats and stats["enough"] and stats["avg90"] > price:
                saving = round((stats["avg90"] - price) / stats["avg90"] * 100, 1)
            offers.append({
                "store": store["id"],
                "store_name": store["name"],
                "store_type": store["type"],
                "url": off["url"],
                "price": round(float(price), 2),
                "currency": off.get("currency", "EUR"),
                "updated": updated.isoformat(),
                "unit_price": unit_price(float(price), prod.get("unit")),
                "avg90": stats["avg90"] if stats and stats["enough"] else None,
                "min90": stats["min90"] if stats and stats["enough"] else None,
                "saving_pct": saving,
                "extras": off.get("extras", {}),
            })

        if not offers:
            skipped["no_valid_offer"] += 1
            continue

        # Mejor precio: por precio unitario si existe, si no por precio total
        def sort_key(o):
            return o["unit_price"]["value"] if o["unit_price"] else o["price"]
        offers.sort(key=sort_key)
        best = offers[0]

        badges = []
        if best["min90"] is not None and best["price"] <= best["min90"]:
            badges.append("Mínimo Histórico")
        elif best["avg90"] is not None and best["price"] < best["avg90"]:
            badges.append("Por debajo de la media de 90 días")
        outlet_prices = [o["price"] for o in offers[1:] if o["store_type"] == "outlet"]
        if best["store_type"] != "outlet" and outlet_prices and best["price"] < min(outlet_prices):
            badges.append("Más barato que en Outlet")
        if section == "packs-ahorro" and best["saving_pct"]:
            badges.append("Pack Ahorro Verificado")
        if best["extras"].get("points_net_saving"):
            badges.append("Ahorro neto con puntos")

        deals.append({
            "id": prod.get("id"),
            "name": prod.get("name"),
            "brand": prod.get("brand", ""),
            "image": prod.get("image", ""),
            "menu": menu,
            "section": section,
            "best": best,
            "others": offers[1:],
            "saving_pct": best["saving_pct"],
            "drop_band": drop_band(best["saving_pct"]),
            "badges": badges,
        })

    deals.sort(key=lambda d: (d["saving_pct"] or 0), reverse=True)
    return deals, skipped


def main():
    ref_time = now_utc()
    errors = []
    try:
        products_raw = load_json("price_history.json", {"products": []})
        stores_raw = load_json("stores.json", {"stores": []})
    except json.JSONDecodeError as exc:
        errors.append("JSON mal formado: %s" % exc)
        products_raw, stores_raw = {"products": []}, {"stores": []}

    stores = evaluate_stores(stores_raw)
    deals, skipped = process(products_raw, stores, ref_time)

    by_menu = {m: 0 for m in MENUS}
    for d in deals:
        by_menu[d["menu"]] += 1

    save_json("deals.json", {
        "generated": ref_time.isoformat(),
        "rules": {
            "saving_vs": "media real de los últimos %d días" % WINDOW_DAYS,
            "max_price_age_hours": MAX_PRICE_AGE_HOURS,
            "min_history_days": MIN_HISTORY_DAYS,
        },
        "stores": list(stores.values()),
        "deals": deals,
    })

    alerts = [{
        "id": d["id"], "name": d["name"], "store": d["best"]["store_name"],
        "price": d["best"]["price"], "avg90": d["best"]["avg90"],
        "saving_pct": d["saving_pct"], "url": d["best"]["url"],
        "badges": d["badges"],
    } for d in deals if (d["saving_pct"] or 0) >= ALERT_MIN_SAVING]
    save_json("alerts.json", {"generated": ref_time.isoformat(), "alerts": alerts})

    save_json("status.json", {
        "engine": "deal_radar_engine",
        "last_run": ref_time.isoformat(),
        "ok": not errors,
        "errors": errors,
        "products_in": len(products_raw.get("products", [])),
        "deals_out": len(deals),
        "alerts_out": len(alerts),
        "stores_total": len(stores),
        "stores_verified": sum(1 for s in stores.values() if s["verified"]),
        "deals_by_menu": by_menu,
        "skipped": skipped,
    })

    print("Motor OK: %d ofertas, %d alertas, %d/%d tiendas verificadas"
          % (len(deals), len(alerts),
             sum(1 for s in stores.values() if s["verified"]), len(stores)))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

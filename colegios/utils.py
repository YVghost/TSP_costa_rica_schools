import math
import pandas as pd
import requests
from datetime import datetime
from itertools import permutations
from functools import lru_cache
from pathlib import Path
from django.conf import settings

LAT_MIN, LAT_MAX = 8.0, 11.5
LON_MIN, LON_MAX = -87.0, -82.0
OSRM_BASE = "http://router.project-osrm.org"

# MEP ArcGIS FeatureServer layers
_ARCGIS_LAYERS = {
    'publico':  'https://services1.arcgis.com/aWQmxJWy7lM2Qqmo/ArcGIS/rest/services/CE_Publicos_CR/FeatureServer/1/query',
    'privado':  'https://services1.arcgis.com/aWQmxJWy7lM2Qqmo/ArcGIS/rest/services/CE_Publicos_CR/FeatureServer/0/query',
}


# ── Coordinate normalization (legacy Excel format) ─────────────────────────────

def _normalize(val, lo, hi):
    """Convert integer-encoded coordinates to decimal degrees."""
    if pd.isna(val):
        return None
    for exp in range(9, -1, -1):
        result = val / (10 ** exp)
        if lo <= result <= hi:
            return round(result, 7)
    return None


# ── Data loading ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_dataframe():
    """
    Load school data from the freshest available source:
    1. data/coordinates/colegios_cr_latest.xlsx  (from MEP ArcGIS API)
    2. Original Excel fallback
    """
    data_dir = Path(settings.DATA_DIR)
    latest   = data_dir / 'colegios_cr_latest.xlsx'
    source   = latest if latest.exists() else settings.EXCEL_DATA_PATH

    df = pd.read_excel(source)
    df = df.dropna(subset=['LATITUD', 'LONGITUD'])
    df['lat'] = df['LATITUD'].apply(lambda x: _normalize(x, LAT_MIN, LAT_MAX))
    df['lon'] = df['LONGITUD'].apply(lambda x: _normalize(x, LON_MIN, LON_MAX))
    df = df.dropna(subset=['lat', 'lon']).reset_index(drop=True)

    # Rename to internal lowercase names (handle both old and new column sets)
    rename_map = {
        'NOMBRE':     'nombre',
        'PROVINCIA':  'provincia',
        'CANTON':     'canton',
        'DISTRITO':   'distrito',
        'ZONA':       'zona',
        'DEPENDENCIA':'dependencia',
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # Fill any column that didn't exist in the source file
    for col in ['nombre', 'provincia', 'canton', 'distrito', 'zona', 'dependencia']:
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].astype(str).str.strip()

    return df[['nombre', 'provincia', 'canton', 'distrito', 'zona', 'dependencia', 'lat', 'lon']]


# ── MEP ArcGIS fetch & save ────────────────────────────────────────────────────

def _fetch_layer(url, page_size=1000):
    """Fetch all features from an ArcGIS FeatureServer layer, handling pagination."""
    features = []
    offset   = 0
    while True:
        resp = requests.get(url, params={
            'where':             '1=1',
            'outFields':         '*',
            'returnGeometry':    'true',
            'outSR':             '4326',
            'f':                 'json',
            'resultOffset':      offset,
            'resultRecordCount': page_size,
        }, timeout=30)
        data  = resp.json()
        batch = data.get('features', [])
        features.extend(batch)
        if not data.get('exceededTransferLimit', False) or not batch:
            break
        offset += len(batch)
    return features


def _feature_to_row(feature, fuente):
    """Flatten a GeoJSON feature into a flat dict with standardized column names."""
    geom  = feature.get('geometry', {})
    attrs = feature.get('attributes', {})
    # geometry.x / geometry.y are always decimal degrees when outSR=4326
    return {
        'NOMBRE':     attrs.get('CENTRO_EDU', ''),
        'PROVINCIA':  attrs.get('PROVINCIA', ''),
        'CANTON':     attrs.get('CANTON', ''),
        'DISTRITO':   attrs.get('DISTRITO', ''),
        'POBLADO':    attrs.get('POBLADO', ''),
        'TIPO':       attrs.get('TIPO_INSTI', ''),
        'ESTADO':     attrs.get('ESTADO', ''),
        'REGIONAL':   attrs.get('REGIONAL', ''),
        'CIRCUITO':   attrs.get('CIRCUITO', ''),
        'LATITUD':    geom.get('y'),   # decimal degrees — _normalize handles 10^0
        'LONGITUD':   geom.get('x'),
        'FUENTE':     fuente,
    }


def fetch_and_save_schools():
    """
    Download all schools from the MEP ArcGIS API, save two Excel files:
      - data/coordinates/colegios_cr_<timestamp>.xlsx  (historical record)
      - data/coordinates/colegios_cr_latest.xlsx       (used by the app)

    Returns a summary dict.
    """
    rows   = []
    counts = {}

    for fuente, url in _ARCGIS_LAYERS.items():
        batch       = _fetch_layer(url)
        counts[fuente] = len(batch)
        rows.extend(_feature_to_row(f, fuente) for f in batch)

    df = pd.DataFrame(rows).dropna(subset=['LATITUD', 'LONGITUD'])

    data_dir = Path(settings.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)

    ts          = datetime.now().strftime('%Y%m%d_%H%M%S')
    timestamped = data_dir / f'colegios_cr_{ts}.xlsx'
    latest      = data_dir / 'colegios_cr_latest.xlsx'

    df.to_excel(timestamped, index=False)
    df.to_excel(latest,      index=False)

    # Invalidate the cached dataframe so the next request loads fresh data
    get_dataframe.cache_clear()

    return {
        'total':    len(df),
        'publicos': counts.get('publico', 0),
        'privados': counts.get('privado', 0),
        'archivo':  timestamped.name,
        'timestamp': ts,
    }


# ── OSRM road-network calls ────────────────────────────────────────────────────

def get_osrm_matrix(coords):
    """
    Road-distance matrix (km) via OSRM Table API.
    coords: list of (lat, lon) — OSRM expects lon,lat in the URL.
    Returns n×n list of lists in km, or None if OSRM is unreachable.
    """
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{OSRM_BASE}/table/v1/driving/{coord_str}?annotations=distance"
    try:
        resp = requests.get(url, timeout=9)
        data = resp.json()
        if data.get('code') == 'Ok':
            return [
                [d / 1000 if d is not None else float('inf') for d in row]
                for row in data['distances']
            ]
    except Exception:
        pass
    return None


def get_osrm_geometry(coords):
    """
    Actual road-route geometry via OSRM Route API.
    coords: list of (lat, lon) in travel order.
    Returns ([lat, lon] polyline points, total_km) or (None, None) on failure.
    """
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{OSRM_BASE}/route/v1/driving/{coord_str}?overview=full&geometries=geojson"
    try:
        resp = requests.get(url, timeout=9)
        data = resp.json()
        if data.get('code') == 'Ok':
            route    = data['routes'][0]
            pts      = [[lat, lon] for lon, lat in route['geometry']['coordinates']]
            total_km = round(route['distance'] / 1000, 2)
            return pts, total_km
    except Exception:
        pass
    return None, None


# ── TSP solver ─────────────────────────────────────────────────────────────────

def solve_tsp(coords, dist_matrix=None):
    """
    Returns (ordered_indices, total_km, algorithm_name).
    Index 0 is always the fixed start point.
    Uses brute-force (exact) for n ≤ 10, nearest-neighbor heuristic otherwise.
    """
    n = len(coords)
    if n <= 1:
        return list(range(n)), 0.0, "—"

    def dist(i, j):
        if dist_matrix:
            return dist_matrix[i][j]
        return haversine(*coords[i], *coords[j])

    if n <= 10:
        return _brute_force(n, dist)
    return _nearest_neighbor(n, dist)


def _brute_force(n, dist_fn):
    best_d    = float('inf')
    best_path = list(range(n))
    for perm in permutations(range(1, n)):
        path = (0,) + perm
        d    = sum(dist_fn(path[i], path[i + 1]) for i in range(n - 1))
        if d < best_d:
            best_d, best_path = d, list(path)
    return best_path, best_d, f"Fuerza Bruta — exacto ({n - 1} colegios, {n - 1}! combinaciones)"


def _nearest_neighbor(n, dist_fn):
    visited = [False] * n
    path    = [0]
    visited[0] = True
    for _ in range(n - 1):
        cur = path[-1]
        nxt = min((i for i in range(n) if not visited[i]), key=lambda i: dist_fn(cur, i))
        path.append(nxt)
        visited[nxt] = True
    total = sum(dist_fn(path[i], path[i + 1]) for i in range(n - 1))
    return path, total, f"Vecino Más Cercano — heurística O(n²) ({n - 1} colegios)"


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

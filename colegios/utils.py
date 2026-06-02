import math
import pandas as pd
import requests
from itertools import permutations
from functools import lru_cache
from django.conf import settings

LAT_MIN, LAT_MAX = 8.0, 11.5
LON_MIN, LON_MAX = -87.0, -82.0
OSRM_BASE = "http://router.project-osrm.org"


def _normalize(val, lo, hi):
    if pd.isna(val):
        return None
    for exp in range(9, -1, -1):
        result = val / (10 ** exp)
        if lo <= result <= hi:
            return round(result, 7)
    return None


@lru_cache(maxsize=1)
def get_dataframe():
    df = pd.read_excel(settings.EXCEL_DATA_PATH)
    df = df.dropna(subset=['LATITUD', 'LONGITUD'])
    df['lat'] = df['LATITUD'].apply(lambda x: _normalize(x, LAT_MIN, LAT_MAX))
    df['lon'] = df['LONGITUD'].apply(lambda x: _normalize(x, LON_MIN, LON_MAX))
    df = df.dropna(subset=['lat', 'lon']).reset_index(drop=True)
    df = df.rename(columns={
        'NOMBRE': 'nombre',
        'PROVINCIA': 'provincia',
        'CANTON': 'canton',
        'DISTRITO': 'distrito',
        'ZONA': 'zona',
        'DEPENDENCIA': 'dependencia',
    })
    # Normalize text to avoid invisible whitespace causing filter mismatches
    for col in ['nombre', 'provincia', 'canton', 'distrito', 'zona']:
        df[col] = df[col].astype(str).str.strip()
    return df[['nombre', 'provincia', 'canton', 'distrito', 'zona', 'dependencia', 'lat', 'lon']]


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ── OSRM road-network calls ────────────────────────────────────────────────────

def get_osrm_matrix(coords):
    """
    Road-distance matrix (km) via OSRM Table API.
    coords: list of (lat, lon) — note OSRM expects lon,lat in the URL.
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
            route = data['routes'][0]
            # GeoJSON coords are [lon, lat] — flip for Leaflet
            pts = [[lat, lon] for lon, lat in route['geometry']['coordinates']]
            total_km = round(route['distance'] / 1000, 2)
            return pts, total_km
    except Exception:
        pass
    return None, None


# ── TSP solver ─────────────────────────────────────────────────────────────────

def solve_tsp(coords, dist_matrix=None):
    """
    Returns (ordered_indices, total_km, algorithm_name).

    coords       – list of (lat, lon) tuples; index 0 is always the fixed start.
    dist_matrix  – optional n×n road-distance matrix (km); falls back to Haversine.

    Brute-force exact solution for n ≤ 10; nearest-neighbor heuristic otherwise.
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
    best_d = float('inf')
    best_path = list(range(n))
    for perm in permutations(range(1, n)):
        path = (0,) + perm
        d = sum(dist_fn(path[i], path[i + 1]) for i in range(n - 1))
        if d < best_d:
            best_d = d
            best_path = list(path)
    return best_path, best_d, f"Fuerza Bruta — exacto ({n - 1} colegios, {n - 1}! combinaciones)"


def _nearest_neighbor(n, dist_fn):
    visited = [False] * n
    path = [0]
    visited[0] = True
    for _ in range(n - 1):
        cur = path[-1]
        nxt = min(
            (i for i in range(n) if not visited[i]),
            key=lambda i: dist_fn(cur, i)
        )
        path.append(nxt)
        visited[nxt] = True
    total = sum(dist_fn(path[i], path[i + 1]) for i in range(n - 1))
    return path, total, f"Vecino Más Cercano — heurística O(n²) ({n - 1} colegios)"

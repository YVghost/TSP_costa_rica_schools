import json
import math
import re
import unicodedata
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

_ARCGIS_LAYERS = {
    'publico': 'https://services1.arcgis.com/aWQmxJWy7lM2Qqmo/ArcGIS/rest/services/CE_Publicos_CR/FeatureServer/1/query',
    'privado': 'https://services1.arcgis.com/aWQmxJWy7lM2Qqmo/ArcGIS/rest/services/CE_Publicos_CR/FeatureServer/0/query',
}

# Known Costa Rica provinces — normalized to ASCII for consistent filtering
_PROVINCE_KNOWN = {
    'ALAJUELA': 'ALAJUELA',
    'CARTAGO': 'CARTAGO',
    'GUANACASTE': 'GUANACASTE',
    'HEREDIA': 'HEREDIA',
    'LIMON': 'LIMON', 'LIMN': 'LIMON',      # LIMÓN → stripped/corrupted
    'PUNTARENAS': 'PUNTARENAS',
    'SAN JOSE': 'SAN JOSE', 'SAN JOS': 'SAN JOSE',  # SAN JOSÉ → stripped/corrupted
}


def _fix_province(s):
    """
    Normalize a Costa Rica province name to clean ASCII.
    Handles accented (LIMÓN), replacement-char corrupted (LIM�N), and plain forms.
    """
    if not isinstance(s, str) or not s.strip():
        return ''
    # Remove Unicode replacement characters (�) left by bad encoding
    s = re.sub(r'�+', '', s).strip().upper()
    # Strip any remaining combining/accent characters
    nfkd = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in nfkd if not unicodedata.combining(c)).strip()
    return _PROVINCE_KNOWN.get(s, s)


# ── Coordinate normalization (legacy Excel integer format) ─────────────────────

def _normalize(val, lo, hi):
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
    1. data/coordinates/colegios_cr_latest.xlsx  (MEP ArcGIS API)
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

    rename_map = {
        'NOMBRE':     'nombre',
        'CODSABER':   'codsaber',
        'CODPRES':    'codpres',
        'CORREO':     'correo',
        'PROVINCIA':  'provincia',
        'CANTON':     'canton',
        'DISTRITO':   'distrito',
        'ZONA':       'zona',
        'DEPENDENCIA':'dependencia',
        'DIRECCION':  'direccion',
        'POBLADO':    'poblado',
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # Ensure all text columns exist, converting NaN → '' before str operations.
    # Note: in pandas 3.x, astype(str) does NOT convert float NaN to 'nan'.
    # fillna('') must come first.
    for col in ['nombre', 'codsaber', 'codpres', 'correo',
                'provincia', 'canton', 'distrito', 'zona', 'dependencia', 'direccion', 'poblado']:
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].fillna('').astype(str).str.strip()

    # Normalize province names (fixes accent/encoding corruption)
    df['provincia'] = df['provincia'].apply(_fix_province)

    # Build a single display address: direccion if present, else poblado
    df['direccion'] = df.apply(
        lambda r: r['direccion'] if r['direccion'] else r['poblado'], axis=1
    )

    return df[['nombre', 'codsaber', 'codpres', 'correo',
               'provincia', 'canton', 'distrito', 'zona', 'dependencia', 'direccion', 'lat', 'lon']]


# ── MEP ArcGIS fetch & save ────────────────────────────────────────────────────

def _fetch_layer(url, page_size=1000):
    """
    Fetch all features from an ArcGIS FeatureServer layer with pagination.
    Falls back to Latin-1 decoding if the server returns non-UTF-8 encoded JSON.
    """
    features = []
    offset   = 0
    params   = {
        'where': '1=1', 'outFields': '*',
        'returnGeometry': 'true', 'outSR': '4326', 'f': 'json',
        'resultRecordCount': page_size,
    }
    while True:
        params['resultOffset'] = offset
        resp = requests.get(url, params=params, timeout=30)

        # ArcGIS servers sometimes send Latin-1 bytes in an otherwise UTF-8 response.
        # Detect by checking for replacement characters after UTF-8 decode.
        try:
            text = resp.content.decode('utf-8')
            if '�' in text:
                raise UnicodeDecodeError('utf-8', b'', 0, 1, 'replacement char')
        except UnicodeDecodeError:
            text = resp.content.decode('latin-1')

        data  = json.loads(text)
        batch = data.get('features', [])
        features.extend(batch)
        if not data.get('exceededTransferLimit', False) or not batch:
            break
        offset += len(batch)
    return features


def _feature_to_row(feature, fuente):
    """Flatten an ArcGIS feature into a flat dict with standardized column names."""
    geom  = feature.get('geometry') or {}
    attrs = feature.get('attributes') or {}
    return {
        'NOMBRE':    attrs.get('CENTRO_EDU') or '',
        'CODSABER':  attrs.get('CODSABER') or '',   # código único MEP (ej. 100517-00)
        'CODPRES':   attrs.get('CODPRES') or '',    # código presupuestario
        'CORREO':    attrs.get('CORREO') or '',     # correo institucional
        'PROVINCIA': attrs.get('PROVINCIA') or '',
        'CANTON':    attrs.get('CANTON') or '',
        'DISTRITO':  attrs.get('DISTRITO') or '',
        'POBLADO':   attrs.get('POBLADO') or '',
        'DIRECCION': attrs.get('DIRECCION') or '',
        'TIPO':      attrs.get('TIPO_INSTI') or '',
        'ESTADO':    attrs.get('ESTADO') or '',
        'REGIONAL':  attrs.get('REGIONAL') or '',
        'CIRCUITO':  attrs.get('CIRCUITO') or '',
        'LATITUD':   geom.get('y'),   # decimal degrees (outSR=4326)
        'LONGITUD':  geom.get('x'),
        'FUENTE':    fuente,
    }


def fetch_and_save_schools():
    """
    Download all schools from the MEP ArcGIS API and save:
      - data/coordinates/colegios_cr_<timestamp>.xlsx  (historical)
      - data/coordinates/colegios_cr_latest.xlsx        (used by the app)
    Returns a summary dict.
    """
    rows   = []
    counts = {}

    for fuente, url in _ARCGIS_LAYERS.items():
        batch          = _fetch_layer(url)
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

    get_dataframe.cache_clear()

    return {
        'total':     len(df),
        'publicos':  counts.get('publico', 0),
        'privados':  counts.get('privado', 0),
        'archivo':   timestamped.name,
        'timestamp': ts,
    }


# ── Group-visit planner (VRP) ─────────────────────────────────────────────────

GROUP_COLORS = ['#e53935', '#1976d2', '#388e3c', '#f57c00',
                '#7b1fa2', '#00838f', '#c2185b', '#5d4037']


def plan_group_visits(start_coords_list, num_groups, minutes_per_visit,
                      hours_workday, df, avg_speed_kmh=40, start_names=None):
    """
    Multi-depot VRP: each group departs from its own start_coords_list[g].

    Matrix layout: [depot_0, ..., depot_{K-1}, school_0, ..., school_{M-1}]
    Group g's depot is always at index g in the matrix.

    Time model
    ----------
    group_time[g] = committed time including return to depot g.
    delta when adding school i from cur:
        drive(cur→i) + visit + drive(i→depot_g) − drive(cur→depot_g)
    """
    max_min = hours_workday * 60
    K = len(start_coords_list)  # number of depots (== num_groups)

    # ── 1. Pre-filter reachable schools (from the nearest depot) ─────
    max_radius_km = avg_speed_kmh * (hours_workday / 2)
    df = df.copy()
    df['_d'] = df.apply(
        lambda r: min(haversine(*sc, r['lat'], r['lon']) for sc in start_coords_list),
        axis=1,
    )
    limit = min(num_groups * 30, 120)
    candidates = (df[df['_d'] <= max_radius_km]
                  .sort_values('_d')
                  .head(limit)
                  .reset_index(drop=True))

    if candidates.empty:
        return []

    # ── 2. Distance matrix ────────────────────────────────────────────
    all_coords = list(start_coords_list) + list(zip(candidates['lat'], candidates['lon']))
    N = len(all_coords)
    matrix = get_osrm_matrix(all_coords)
    if matrix is None:
        matrix = [[haversine(*all_coords[i], *all_coords[j])
                   for j in range(N)] for i in range(N)]

    def drive_min(i, j):
        return matrix[i][j] / avg_speed_kmh * 60

    # ── 3. Greedy round-robin VRP ─────────────────────────────────────
    assigned   = [False] * len(candidates)
    group_path = [[] for _ in range(num_groups)]
    group_time = [0.0] * num_groups
    group_last = list(range(K))   # group g starts at depot index g

    improved = True
    while improved:
        improved = False
        for g in range(num_groups):
            best_i, best_delta = None, float('inf')
            cur   = group_last[g]
            depot = g                # depot index for group g
            for i, _ in enumerate(candidates.itertuples()):
                if assigned[i]:
                    continue
                ci       = K + i    # school index in the combined matrix
                old_ret  = drive_min(cur, depot)
                drive_to = drive_min(cur, ci)
                new_ret  = drive_min(ci,  depot)
                delta    = drive_to + minutes_per_visit + new_ret - old_ret
                if group_time[g] + delta <= max_min and drive_to < best_delta:
                    best_delta = drive_to
                    best_i     = i

            if best_i is not None:
                ci       = K + best_i
                cur      = group_last[g]
                depot    = g
                old_ret  = drive_min(cur, depot)
                drive_to = drive_min(cur, ci)
                new_ret  = drive_min(ci,  depot)
                group_time[g] += drive_to + minutes_per_visit + new_ret - old_ret
                group_last[g]  = ci
                group_path[g].append(best_i)
                assigned[best_i] = True
                improved = True

    # ── 4. TSP-optimise + geometry per group ─────────────────────────
    result = []
    for g_idx in range(num_groups):
        school_idxs  = group_path[g_idx]
        depot_coords = start_coords_list[g_idx]
        depot_name   = (start_names[g_idx] if start_names else None) or f'Grupo {g_idx + 1}'

        if not school_idxs:
            result.append({
                'grupo':                g_idx + 1,
                'color':                GROUP_COLORS[g_idx % len(GROUP_COLORS)],
                'colegios':             [], 'num_colegios': 0,
                'distancia_km':         0,  'tiempo_total_min': 0,
                'tiempo_conduccion_min': 0, 'tiempo_visitas_min': 0,
                'geometry':             None,
            })
            continue

        grp_coords = [depot_coords] + [
            (candidates.iloc[i]['lat'], candidates.iloc[i]['lon'])
            for i in school_idxs
        ]
        all_idx    = [g_idx] + [K + i for i in school_idxs]
        sub_matrix = [[matrix[a][b] for b in all_idx] for a in all_idx]

        path, total_km, _ = solve_tsp(grp_coords, sub_matrix)

        geometry, road_km = get_osrm_geometry([grp_coords[p] for p in path])
        if road_km:
            total_km = road_km

        drive_m = round(total_km / avg_speed_kmh * 60)
        visit_m = len(school_idxs) * minutes_per_visit

        colegios = []
        for order, p in enumerate(path):
            if p == 0:
                colegios.append({
                    'orden':     order + 1,
                    'nombre':    depot_name,
                    'lat':       depot_coords[0],
                    'lon':       depot_coords[1],
                    'es_inicio': True,
                })
            else:
                row = candidates.iloc[school_idxs[p - 1]]
                colegios.append({
                    'orden':     order + 1,
                    'nombre':    row['nombre'],
                    'canton':    row['canton'],
                    'provincia': row['provincia'],
                    'codsaber':  row['codsaber'],
                    'direccion': row['direccion'],
                    'lat':       float(row['lat']),
                    'lon':       float(row['lon']),
                    'es_inicio': False,
                })

        result.append({
            'grupo':                g_idx + 1,
            'color':                GROUP_COLORS[g_idx % len(GROUP_COLORS)],
            'colegios':             colegios,
            'num_colegios':         len(school_idxs),
            'distancia_km':         round(total_km, 1),
            'tiempo_conduccion_min': drive_m,
            'tiempo_visitas_min':   visit_m,
            'tiempo_total_min':     drive_m + visit_m,
            'geometry':             geometry,
        })

    return result


# ── OSRM road-network calls ────────────────────────────────────────────────────

def get_osrm_matrix(coords):
    """Road-distance matrix (km) via OSRM Table API. Returns None if unreachable."""
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{OSRM_BASE}/table/v1/driving/{coord_str}?annotations=distance"
    try:
        data = requests.get(url, timeout=9).json()
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
    Returns ([lat, lon] polyline, total_km) or (None, None) on failure.
    """
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{OSRM_BASE}/route/v1/driving/{coord_str}?overview=full&geometries=geojson"
    try:
        data = requests.get(url, timeout=9).json()
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
    Index 0 is the fixed start. Brute-force for n ≤ 10, nearest-neighbor otherwise.
    """
    n = len(coords)
    if n <= 1:
        return list(range(n)), 0.0, "—"

    def dist(i, j):
        return dist_matrix[i][j] if dist_matrix else haversine(*coords[i], *coords[j])

    return _brute_force(n, dist) if n <= 10 else _nearest_neighbor(n, dist)


def _brute_force(n, dist_fn):
    best_d, best_path = float('inf'), list(range(n))
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
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

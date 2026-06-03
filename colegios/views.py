import json
import datetime
from pathlib import Path
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .utils import get_dataframe, solve_tsp, get_osrm_matrix, get_osrm_geometry, fetch_and_save_schools


def _data_source_info():
    """Return info about which data file the app is currently using."""
    latest = Path(settings.DATA_DIR) / 'colegios_cr_latest.xlsx'
    if latest.exists():
        mtime = datetime.datetime.fromtimestamp(latest.stat().st_mtime)
        return {
            'fuente': 'MEP ArcGIS',
            'actualizado': mtime.strftime('%d/%m/%Y %H:%M'),
            'es_original': False,
        }
    return {
        'fuente': 'Excel original',
        'actualizado': None,
        'es_original': True,
    }


def index(request):
    df = get_dataframe()
    provincias = sorted(p for p in df['provincia'].unique() if p)
    return render(request, 'colegios/index.html', {
        'provincias':     provincias,
        'total_colegios': len(df),
        'data_info':      _data_source_info(),
    })


@require_GET
def api_colegios(request):
    df = get_dataframe()
    provincia = request.GET.get('provincia', '').strip()
    q = request.GET.get('q', '').strip()

    filtered = df
    if provincia:
        # strip both sides so invisible whitespace never causes a mismatch
        filtered = filtered[filtered['provincia'].str.strip() == provincia.strip()]
    if q:
        filtered = filtered[
            filtered['nombre'].str.contains(q, case=False, na=False, regex=False)
        ]

    records = (
        filtered[['nombre', 'provincia', 'canton', 'zona', 'direccion', 'lat', 'lon']]
        .sort_values('nombre')
        .fillna('')          # NaN in any text column would break JSON serialization
        .to_dict('records')
    )
    return JsonResponse({'colegios': records, 'count': len(records)})


@csrf_exempt
@require_POST
def api_ruta(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    nombres = data.get('nombres', [])
    # punto_partida is optional: {lat, lon, label}
    punto_partida = data.get('punto_partida')

    if len(nombres) < 2:
        return JsonResponse({'error': 'Seleccioná al menos 2 colegios'}, status=400)
    if len(nombres) > 14:
        return JsonResponse({'error': 'Máximo 14 colegios por ruta'}, status=400)

    df = get_dataframe()
    sel = df[df['nombre'].isin(nombres)].reset_index(drop=True)

    if len(sel) < 2:
        return JsonResponse({'error': 'No se encontraron los colegios indicados'}, status=404)

    # Build coordinate list: starting point (index 0) + schools
    has_start = punto_partida is not None
    if has_start:
        all_coords = [(punto_partida['lat'], punto_partida['lon'])] + list(zip(sel['lat'], sel['lon']))
    else:
        all_coords = list(zip(sel['lat'], sel['lon']))

    # Attempt OSRM road-distance matrix; fall back to Haversine on failure
    dist_matrix = get_osrm_matrix(all_coords)
    usando_calles = dist_matrix is not None

    path, total_km, algoritmo = solve_tsp(all_coords, dist_matrix)

    # Build ordered stop list
    ruta = []
    for order, idx in enumerate(path):
        if has_start and idx == 0:
            ruta.append({
                'orden': order + 1,
                'nombre': punto_partida.get('label', 'Punto de partida'),
                'canton': '',
                'provincia': '',
                'lat': punto_partida['lat'],
                'lon': punto_partida['lon'],
                'es_inicio': True,
            })
        else:
            sel_idx = (idx - 1) if has_start else idx
            row = sel.iloc[sel_idx]
            ruta.append({
                'orden': order + 1,
                'nombre': row['nombre'],
                'canton': row['canton'],
                'provincia': row['provincia'],
                'lat': float(row['lat']),
                'lon': float(row['lon']),
                'es_inicio': False,
            })

    # Get actual road geometry in the resolved travel order
    ordered_coords = [(s['lat'], s['lon']) for s in ruta]
    geometry, road_km = get_osrm_geometry(ordered_coords)

    if road_km is not None:
        total_km = road_km

    return JsonResponse({
        'ruta': ruta,
        'total_km': round(total_km, 2),
        'algoritmo': algoritmo,
        'geometry': geometry,
        'usando_calles': usando_calles,
    })


@csrf_exempt
@require_POST
def api_actualizar_datos(request):
    """Fetch fresh school data from the MEP ArcGIS API and save to data/coordinates/."""
    try:
        summary = fetch_and_save_schools()
        return JsonResponse(summary)
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)

# Colegios Costa Rica — Mapa y Ruta Óptima

Aplicación web Django para visualizar la ubicación geográfica de colegios de Costa Rica (IPEC, CINDEA, CONED) sobre un mapa interactivo y calcular la ruta más corta entre colegios seleccionados, usando la red vial real del país.

## Funcionalidades

- Mapa interactivo con los 5 259 colegios obtenidos del portal MEP-ArcGIS (públicos y privados)
- Filtro por provincia y búsqueda por nombre en tiempo real
- Selección de colegios haciendo click en el mapa o en la lista lateral (máximo 14)
- Tooltip al pasar el cursor: nombre, dirección, cantón y coordenadas GPS
- **Punto de partida configurable**: hacé click en «Colocar» y luego en cualquier punto del mapa; el marcador es arrastrable
- **Ruta por calles reales**: trazado y distancias calculados sobre la red vial de Costa Rica vía OSRM + OpenStreetMap; si OSRM no está disponible, usa distancia en línea recta como respaldo
- **Actualizar datos MEP**: descarga los datos frescos del portal ArcGIS con un botón, sin necesidad de tocar código

---

## Algoritmos TSP (Problema del Viajante)

El problema de visitar N colegios en el orden más eficiente es el **Traveling Salesman Problem (TSP)**: encontrar la permutación de paradas que minimiza la distancia total recorrida.

### Por qué es difícil

Con N colegios hay `(N-1)!` posibles rutas. Para N=10 eso son 362 880 combinaciones; para N=15 son más de 87 mil millones. No existe un algoritmo que resuelva el TSP de forma exacta en tiempo polinomial (es un problema NP-difícil), por lo que la estrategia depende del tamaño de la entrada.

### Algoritmos implementados

#### Fuerza Bruta — solución exacta (n ≤ 10)

Se fija el punto de inicio (índice 0) y se generan todas las permutaciones posibles de los colegios restantes. Se elige la permutación con menor distancia total.

```
Complejidad: O((n-1)!)
Colegios | Permutaciones | Tiempo estimado
       3 |             2 | < 1 ms
       5 |            24 | < 1 ms
       8 |         5 040 | < 1 ms
      10 |       362 880 | < 10 ms
```

Para el caso de uso habitual (visitar 3–8 colegios) este algoritmo da la **respuesta perfecta en tiempo imperceptible**.

#### Vecino más cercano — heurística rápida (n > 10)

Algoritmo greedy: desde el punto de inicio, siempre se avanza al colegio más cercano que no haya sido visitado todavía.

```
Complejidad: O(n²)
Calidad: ~85–90 % del óptimo en la práctica
```

No garantiza la solución óptima, pero es extremadamente rápido para cualquier tamaño de N y produce rutas razonables para planificación real.

### Métrica de distancia

Las distancias se calculan de dos formas, en este orden de preferencia:

1. **Matriz OSRM** — se consulta la API pública de OSRM con los N puntos seleccionados; devuelve una matriz N×N de distancias reales por carretera en kilómetros. El TSP usa esa matriz para encontrar la permutación óptima según kilómetros reales.
2. **Haversine** (respaldo) — si OSRM no responde, se usa la fórmula de la distancia de arco sobre la esfera terrestre (línea recta en km). El badge en el panel de resultados indica cuál se usó.

Una vez determinado el orden óptimo, se llama al endpoint **Route** de OSRM para obtener la geometría detallada de la ruta (las curvas de las calles reales) y trazar la línea en el mapa.

---

## Fuente de datos y actualización

### Portal ArcGIS del MEP

Los datos provienen del servicio REST público del Ministerio de Educación Pública de Costa Rica:

```
https://services1.arcgis.com/aWQmxJWy7lM2Qqmo/ArcGIS/rest/services/CE_Publicos_CR/FeatureServer
```

| Layer | Contenido | Registros |
|---|---|---|
| `FeatureServer/1` | CE_PUBLICOS — centros educativos públicos | ~4 667 |
| `FeatureServer/0` | CE_PRIVADO — centros educativos privados | ~592 |

### Campos que se guardan en el Excel

Cada registro descargado del ArcGIS se normaliza y guarda con las siguientes columnas:

| Campo | Descripción | Ejemplo |
|---|---|---|
| `NOMBRE` | Nombre del centro educativo | `ABRAHAM LINCOLN` |
| `CODSABER` | Código único MEP (identificador oficial) | `100567-00` |
| `CODPRES` | Código presupuestario | `358` |
| `CORREO` | Correo electrónico institucional | `esc.abrahamlincoln@mep.go.cr` |
| `PROVINCIA` | Provincia | `SAN JOSE` |
| `CANTON` | Cantón | `ESCAZU` |
| `DISTRITO` | Distrito | `SAN ANTONIO` |
| `POBLADO` | Poblado o comunidad | `EL CARMEN` |
| `DIRECCION` | Dirección física | `25 SUR DE IGLESIA CATÓLICA` |
| `TIPO` | Tipo de institución (`PÚBLICO` / `PRIVADO`) | `PÚBLICO` |
| `ESTADO` | Estado del centro (`ACTIVO` / `INACTIVO`) | `ACTIVO` |
| `REGIONAL` | Dirección regional del MEP | `DIRECCIÓN REGIONAL SAN JOSÉ OESTE` |
| `CIRCUITO` | Circuito educativo | `CIRCUITO 03` |
| `LATITUD` | Latitud decimal (WGS84) | `9.896753` |
| `LONGITUD` | Longitud decimal (WGS84) | `-84.141212` |
| `FUENTE` | Origen del registro (`publico` / `privado`) | `publico` |

### Cómo se muestran los códigos en el mapa

Al pasar el cursor sobre cualquier punto del mapa aparece un tooltip con:

```
Nombre del Colegio
📍 Dirección física
Cantón, PROVINCIA
CODSABER: 100567-00  |  CODPRES: 358
✉ correo@mep.go.cr
🌐 9.89675, -84.14121
```

### Cómo funciona la descarga

Al pulsar el botón **Actualizar datos MEP**:

1. Se realiza una petición POST a `/api/actualizar-datos/`.
2. Django llama a `fetch_and_save_schools()` en `utils.py`, que itera los dos layers.
3. Cada layer se descarga con paginación automática (`resultOffset` / `resultRecordCount`) de 1 000 registros por página.
4. Los parámetros usados en cada petición:
   - `where=1=1` — trae todos los registros sin filtro
   - `outFields=*` — devuelve todos los campos (incluye CODSABER, CODPRES, CORREO)
   - `returnGeometry=true` — incluye las coordenadas del punto
   - `outSR=4326` — coordenadas en WGS84 (latitud/longitud decimal estándar)
   - `f=json` — respuesta en formato JSON de ArcGIS
5. Las coordenadas se extraen directamente de `geometry.x` (longitud) y `geometry.y` (latitud), que ya vienen en grados decimales porque se especificó `outSR=4326`.
6. Se normaliza el encoding del texto (el servidor puede responder en Latin-1 en lugar de UTF-8).
7. Se guardan dos archivos Excel en `data/coordinates/`:
   - `colegios_cr_YYYYMMDD_HHMMSS.xlsx` — registro histórico con timestamp
   - `colegios_cr_latest.xlsx` — el que usa la app en todo momento
8. Se invalida el caché de `get_dataframe()` para que la próxima carga use los datos frescos.
9. El encabezado del sidebar se actualiza con el nuevo total y la fecha de actualización, sin recargar la página.

### Prioridad de fuente de datos al iniciar

```
data/coordinates/colegios_cr_latest.xlsx   ← si existe, se usa este
        ↓ si no existe
Gonglomerados_colegios_ipec_cindea_coned.xlsx  ← fallback (Excel original, 691 registros)
```

El encabezado muestra `☁ DD/MM/YYYY HH:MM` cuando usa datos del MEP, o `⚠ Sin actualizar` cuando usa el fallback.

---

## Requisitos

- Python 3.10+
- Conexión a internet para el mapa (CartoDB), el ruteo (OSRM) y la descarga de datos (ArcGIS MEP)
- Las dependencias están listadas en `requirements.txt`

## Instalación

```bash
# Crear entorno virtual
python -m venv venv

# Activar entorno (Windows)
.\venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt
```

## Levantar el servidor

```bash
.\venv\Scripts\python.exe manage.py runserver
```

Abrí el navegador en `http://127.0.0.1:8000`.

Al iniciar por primera vez sin datos descargados, pulsá **Actualizar datos MEP** para obtener los 5 000+ colegios desde el portal del MEP.

---

## Endpoints de la API

| Método | URL | Descripción |
|---|---|---|
| `GET` | `/` | Página principal con mapa |
| `GET` | `/api/colegios/` | Lista de colegios (acepta `?provincia=` y `?q=`) |
| `POST` | `/api/ruta/` | Calcula la ruta óptima (`nombres[]`, `punto_partida` opcional) |
| `POST` | `/api/actualizar-datos/` | Descarga datos frescos del MEP ArcGIS |

## Estructura del proyecto

```
camino_menor_peso/
├── Gonglomerados_colegios_ipec_cindea_coned.xlsx  # Fallback de datos (Excel original)
├── data/
│   └── coordinates/
│       ├── colegios_cr_latest.xlsx        # Datos activos (MEP ArcGIS)
│       └── colegios_cr_YYYYMMDD_HHMMSS.xlsx  # Histórico por descarga
├── manage.py
├── requirements.txt
├── colegios_cr/          # Configuración Django
│   ├── settings.py
│   └── urls.py
└── colegios/             # Aplicación principal
    ├── utils.py          # Datos, normalización, OSRM, algoritmos TSP, fetch ArcGIS
    ├── views.py          # Endpoints de la API
    ├── urls.py
    └── templates/
        └── colegios/
            └── index.html
```

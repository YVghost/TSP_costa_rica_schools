# Colegios Costa Rica — Mapa y Ruta Óptima

Aplicación web Django para visualizar la ubicación geográfica de colegios de Costa Rica (IPEC, CINDEA, CONED) sobre un mapa interactivo y calcular la ruta más corta entre colegios seleccionados, usando la red vial real del país.

## Funcionalidades

- Mapa interactivo con los 691 colegios que tienen coordenadas válidas
- Filtro por provincia y búsqueda por nombre en tiempo real (la selección se limpia al cambiar de provincia)
- Selección de colegios haciendo click en el mapa o en la lista lateral (máximo 14)
- **Punto de partida configurable**: hacé click en «Colocar» y luego en cualquier punto del mapa; el marcador es arrastrable para ajustar la posición
- **Ruta por calles reales**: distancias y geometría calculadas sobre la red vial de Costa Rica vía OSRM + OpenStreetMap; si OSRM no está disponible, cae automáticamente a distancia en línea recta
- Visualización de la ruta con marcadores numerados y trazado sobre las calles reales

## Algoritmo de ruta

El problema de visitar N colegios en el orden más eficiente es el **Problema del Viajante (TSP)**. La app usa:

| Puntos totales (inicio + colegios) | Algoritmo | Resultado |
|---|---|---|
| ≤ 10 | Fuerza Bruta — revisa todas las permutaciones | Solución exacta y óptima |
| > 10 | Vecino Más Cercano — siempre va al más cercano no visitado | Aproximación rápida (~85-90% óptimo) |

Las distancias se obtienen de la **matriz OSRM** (kilómetros reales por carretera). Si OSRM no responde, se usa **Haversine** (línea recta sobre la superficie terrestre) como respaldo. El badge en el resultado indica cuál se usó.

## Datos

El archivo fuente es `Gonglomerados_colegios_ipec_cindea_coned.xlsx`. Las filas con latitud o longitud nulas son descartadas automáticamente al iniciar. Las coordenadas están almacenadas como enteros (×10⁶–×10⁸) y se normalizan a grados decimales al cargar.

## Requisitos

- Python 3.10+
- Conexión a internet para cargar el mapa (CartoDB) y para el ruteo por calles (OSRM)
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

Luego abrí el navegador en:

```
http://127.0.0.1:8000
```

## Endpoints

| Método | URL | Descripción |
|---|---|---|
| `GET` | `/` | Página principal con mapa |
| `GET` | `/api/colegios/` | Lista de colegios (acepta `?provincia=` y `?q=`) |
| `POST` | `/api/ruta/` | Calcula la ruta óptima (body JSON: `nombres`, `punto_partida` opcional) |

## Estructura del proyecto

```
camino_menor_peso/
├── Gonglomerados_colegios_ipec_cindea_coned.xlsx  # Datos de colegios
├── manage.py
├── requirements.txt
├── colegios_cr/          # Configuración Django
│   ├── settings.py
│   └── urls.py
└── colegios/             # Aplicación principal
    ├── utils.py          # Carga de datos, normalización, OSRM, algoritmos TSP
    ├── views.py          # Endpoints: /, /api/colegios/, /api/ruta/
    ├── urls.py
    └── templates/
        └── colegios/
            └── index.html
```

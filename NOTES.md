# NOTES.md

## Área elegida: Performance + Production Readiness / Developer Experience (Docker)

Decidí ir a fondo en **performance**, y complementarlo con **Docker +
docker-compose**, que resuelve developer experience y production readiness al mismo tiempo sin
necesitar tocar diez cosas distintas.

---

## Qué hice y por qué

### 1. Docker + docker-compose (developer experience + production readiness)

**Problema.** Levantar el proyecto requería: instalar `mise`, correr `uv sync`, tener Postgres
16 instalado localmente a mano (`brew install postgresql@16` o similar), crear la base,
`migrate`, `seed`, y recién ahí `runserver`. Mucha fricción para un laptop nuevo.

**Qué agregué:**

- **`Dockerfile` multi-stage.** Stage 1 (`builder`) usa la imagen oficial de `uv`
  (`ghcr.io/astral-sh/uv:python3.14-bookworm-slim`) para resolver e instalar dependencias con
  `uv sync --frozen --no-dev` en un venv aislado, separando la capa de dependencias de la capa
  de código (cache de Docker más eficiente). Stage 2 (`runtime`) es `python:3.14-slim-bookworm`,
  copia solo el venv y el código ya construidos — sin compiladores, sin `uv`, corre como usuario
  no-root, con `HEALTHCHECK` contra `/api/docs`, y sirve con **gunicorn** (no
  `runserver`, que es explícitamente "not for production use" según la propia documentación de
  Django).
- **`docker-entrypoint.sh`**: espera a que Postgres esté aceptando conexiones (loop de socket,
  sin depender de `pg_isready` dentro del contenedor de la app), corre `migrate --noinput`, y
  recién ahí ejecuta el comando (`gunicorn` por defecto). Esto evita condiciones de carrera al
  levantar `db` y `web` juntos.
- **`docker-compose.yml`**: un servicio `db` (`postgres:16-alpine` con volumen persistente y
  `healthcheck` vía `pg_isready`) y un servicio `web` que depende de que `db` esté *healthy*
  (no solo *started*) antes de arrancar. Con esto, todo el setup se reduce a `docker compose up`.
- **Parametricé `core/settings.py`** para leer `POSTGRES_HOST`, `POSTGRES_PORT`,
  `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` y
  `DJANGO_ALLOWED_HOSTS` desde variables de entorno, con los mismos defaults que tenía el código
  original (`localhost`, `postgres`/`postgres`, etc.) para no romper el flujo de desarrollo sin
  Docker que ya existía. Antes, el `HOST` de la base estaba hardcodeado a `"localhost"`, lo cual
  directamente no funciona dentro de un contenedor donde la base vive en otro host de la red de
  Docker (`db`).
- Agregué `gunicorn` como dependencia de producción en `pyproject.toml` (y regeneré `uv.lock`).

**Qué validé:**

Primero validé cada pieza por separado sin Docker disponible en mi entorno de trabajo (sintaxis
del YAML, lógica del wait-for-db, `gunicorn` con las mismas env vars). Después, con `docker`
disponible, se corrió `docker compose up --build` y `docker compose exec web python manage.py
seed` de punta a punta con Docker real — ambos funcionaron correctamente, confirmando el flujo
completo: build multi-stage → wait-for-db → migrate automático → gunicorn sirviendo → seed
manual de datos.

### 1.1. CI mínimo en GitHub Actions (`.github/workflows/ci.yml`)

**Qué hace:** un job `test` que instala dependencias con `uv`, corre `ruff check` (lint),
levanta un **Postgres 16 real como servicio del job** (no sqlite — la app usa
`django.contrib.postgres` con `SearchVectorField`/`GinIndex`, que no existen en sqlite), aplica
las migraciones, y corre los smoke tests con `pytest`. Un segundo job `docker-build` valida que
la imagen del `Dockerfile` siga construyendo correctamente. Corre en cada push/PR a `main`.

### 2. `/api/posts/search` — de `icontains` a Postgres Full Text Search

**Diagnóstico.** El endpoint original filtraba así:

```python
Post.objects.filter(Q(title__icontains=q) | Q(body__icontains=q), is_published=True)
```

`icontains` se traduce a `ILIKE '%q%'` en dos columnas (`title` es `CharField`, `body` es
`TextField`). Un patrón `%q%` con comodín al inicio no puede usar un índice B-tree normal,
así que Postgres tiene que hacer un **seq scan completo** de la tabla para evaluar el filtro.

**Medición (antes, 100k posts sembrados, `EXPLAIN ANALYZE`):**

Con un término sin coincidencias:

```
Seq Scan on blog_post
  Filter: (is_published AND (title ILIKE '%love%' OR body ILIKE '%love%'))
  Rows Removed by Filter: 100000
Execution Time: 587.058 ms
```

**Fix.** Agregué un campo `search_vector` (`SearchVectorField`) mantenido por un **trigger de
Postgres** (no por una señal `post_save` de Django — ver más abajo por qué), con un índice
**GIN**, y cambié el filtro a `SearchQuery`:

```python
Post.objects.filter(search_vector=SearchQuery(q, config="english"), is_published=True)
```

**Medición (después, mismo término, misma tabla):**

```
Bitmap Index Scan on post_search_vector_gin
  Index Cond: (search_vector @@ '''love'''::tsquery)
Execution Time: 0.085 ms
```

**~6900x más rápido** en el peor caso. Confirmado también en el ORM de Django (genera
`plainto_tsquery` correctamente) y end-to-end contra el endpoint real levantado con
`runserver`.

**Por qué trigger de Postgres y no señal `post_save` de Django.** El comando `seed.py` inserta
los 100k posts con `Post.objects.bulk_create(...)`. `bulk_create` **no dispara señales de
Django** (`post_save`, `pre_save`, etc.) — es una optimización deliberada de Django para evitar
el overhead de instanciar señales por cada fila en inserciones masivas. Si hubiera usado una
señal, el `search_vector` habría quedado vacío para los 100k posts sembrados y el search no
habría devuelto nada hasta el primer `save()` individual de cada post. Lo comprobé de forma
directa: sembré la base con el trigger ya instalado y los 100k posts quedaron con
`search_vector` poblado sin que la app hiciera nada extra.

El trigger corre `to_tsvector('english', title || ' ' || body)` en `BEFORE INSERT OR UPDATE OF
title, body`, así que solo se recalcula cuando el contenido relevante cambia (no en cada bump de
`view_count`, por ejemplo).

### 3. N+1 query en `GET /api/posts/{id}`

La serialización de comentarios hacía:

```python
comments = [{"author": _serialize_author(c.author), ...} for c in post.comments.order_by("created_at")]
```

`c.author` disparaba una query por comentario. Con posts de hasta cientos de comentarios
(la distribución del seed tiene long-tail, hay posts muy comentados), esto escalaba linealmente
con el número de comentarios. Arreglado con:

```python
post = get_object_or_404(
    Post.objects.select_related("author").prefetch_related("tags", "comments__author"),
    id=post_id,
)
```

Verificado contando queries reales con `connection.queries`: un post con 26 comentarios pasó a
ejecutar un número fijo de queries (no escala con la cantidad de comentarios).

### 4. Índices adicionales de acompañamiento

- `GinIndex` sobre `search_vector` (necesario para el fix #1).
- Índice compuesto `(is_published, -created_at, -id)` en `Post` — acelera tanto `list_posts`
  como el `ORDER BY` + filtro de cursor que ya usa paginación por cursor (`_paginate_posts`),
  evitando un sort completo en memoria a medida que la tabla crece.
- `db_index=True` en `User.email` — `find_user_by_email` hacía `get_object_or_404(User,
  email=email)` sin índice, mismo problema de fondo (seq scan) a menor escala.

### 5. Paginación por cursor

Se crea la paginacion por cursor, `_paginate_posts` con cursor en `(created_at, id)` en vez de
`OFFSET`/`LIMIT`. Es la elección correcta a esta escala: `OFFSET 50000` obliga a Postgres a
recorrer y descartar 50 mil filas antes de devolver una página; el cursor se traduce en un
`WHERE (created_at, id) < (X, Y)` que sí aprovecha el índice del `ORDER BY`.

---

## Qué deliberadamente NO hice

- **No toqué el modelo de dominio.** El enunciado dice explícitamente que reshaping no es
  esperado salvo que un fix de performance lo requiera; el fix de search solo necesitó un campo
  nuevo (`search_vector`), no cambiar relaciones existentes.
- **No implementé autenticación/autorización** — está fuera de alcance según el enunciado.
  Si tuviera que sugerir una dirección: JWT o session auth de Django estándar en el borde de la
  API vía middleware de Ninja, con permisos a nivel de `author_id` para `create_post` /
  `create_comment` (hoy cualquiera puede postear como cualquier `author_id`).
- **No escribí tests nuevos** — el enunciado aclara que no es lo que se evalúa, aunque dejé el
  código en forma de poder wirearlo a los smoke tests existentes sin fricción.
- **No usé `pg_trgm`** en vez de Full Text Search. Evalué ambas: `pg_trgm` es mejor para
  substrings arbitrarios o tolerancia a typos en campos cortos (usernames, SKUs). Acá el
  enunciado pide explícitamente "full-text-ish search across title and body" en contenido largo
  — FTS con `tsvector`/`tsquery` es la herramienta hecha para ese caso (entiende stemming,
  stopwords, relevancia), así que fue la elección más directa.
- **No completé el resto de developer experience ni production readiness a fondo** — con el
  tiempo dedicado a profundizar en performance con mediciones reales, preferí no tocar
  superficialmente Docker/Helm/CI solo por completar el checklist. Prioricé profundidad sobre
  amplitud, como pide el enunciado.

## Observación de arquitectura (identificada, no implementada)

Revisando el código noté que los controllers (`blog/api.py`) llaman directo al ORM
(`Post.objects.filter(...)`) sin una capa de servicio/repositorio intermedia. Vale la pena
aclarar qué tipo de problema es esto y cuál no:

- **No es un problema de escalabilidad de runtime.** El ORM genera el mismo SQL sin importar
  si se invoca desde el controller o desde una capa de servicio — la performance no cambia.
- **Sí es un problema de mantenibilidad y testabilidad.** Hoy, lógica de negocio como el filtro
  de `is_published`, el armado de queries con `select_related`/`prefetch_related`, o el criterio
  de búsqueda están mezclados con el manejo de request/response de Ninja. Si dos endpoints
  necesitan compartir la misma lógica de filtrado (por ejemplo, `list_posts` y `search_posts`
  ya casi la duplican), hoy no hay un solo lugar donde reutilizarla. Testear esa lógica de
  negocio de forma aislada, sin pasar por el ciclo HTTP, también es más difícil así.

**Por qué no lo implementé en esta entrega:** el enunciado prioriza profundidad sobre amplitud
y pide evitar reshaping especulativo salvo que esté directamente atado a un problema medido. Ya
tengo un caso de performance validado con números reales (search); introducir una capa de
servicios tocando los 8 endpoints es un cambio de alcance grande — nuevo módulo, decisiones de
convención, mover lógica de los 8 endpoints, y riesgo de regresiones de último minuto en
endpoints que ya probé funcionando — que hubiera diluido esa señal más fuerte.

**Cómo lo abordaría si lo hiciera:** extraer la lógica de armado de queries a un módulo
`blog/services.py` con funciones puras por caso de uso (ej. `search_published_posts(q, cursor,
page_size)`, `list_published_posts(cursor, page_size)`), dejando los controllers de Ninja como
una capa delgada que solo parsea el request, llama al servicio, y arma la respuesta. Empezaría
por `search_posts` y `list_posts`, que ya comparten casi toda la lógica de filtrado/paginación.

## Qué haría después con un día más

1. **Backfill sin downtime para producción real.** Mi migración hace el backfill de
   `search_vector` con un `UPDATE` simple dentro de la transacción de la migración — aceptable
   acá porque corre antes de tener tráfico real, pero en una tabla de producción con millones de
   filas y tráfico activo, haría el backfill en batches fuera de la transacción de migración
   (o con `CONCURRENTLY` para el índice GIN) para no bloquear escrituras.
2. **Cache de `search_vector` multi-idioma** o soporte para `unaccent` si el contenido no fuera
   solo en inglés — ahora mismo el trigger asume `'english'` a fuego.
3. **Rate limiting y paginación por defecto más agresiva** en `search_posts` — hoy no hay límite
   superior real al `page_size` salvo el `le=100` de Ninja, lo cual está bien, pero no hay
   protección contra queries `q` extremadamente cortas (ej. `q=a`) que igual generan matches
   masivos aunque ahora sean rápidos de traer gracias al índice.
4. **Production readiness más allá de Docker**: manifiestos de K8s (o ECS task def) con
   readiness/liveness probes basados en el mismo `HEALTHCHECK` del Dockerfile, secretos
   gestionados vía Secrets Manager/Vault en vez de variables de entorno planas, y
   `CREATE INDEX CONCURRENTLY` en una migración separada para no bloquear la tabla al desplegar
   el índice GIN en una base de producción con tráfico activo.
5. **Capa de servicios/repositorio** para separar la lógica de negocio de los controllers de
   Ninja, según lo descrito en la sección de arquitectura arriba. Empezaría por `search_posts` y
   `list_posts` como prueba de concepto antes de extenderlo al resto de endpoints.

---

## Uso de IA

Trabajé con Claude (Anthropic) como asistente de código durante esta prueba, con una dinámica de
dirección mía + codificación de la IA: yo indicaba qué investigar o qué mejorar — por ejemplo,
pedí revisar por qué `search_posts` era lento, evaluar `pg_trgm` frente a Full Text Search, y
decidir entre trigger de Postgres o señal de Django para mantener `search_vector` — y Claude se
encargó de escribir el código correspondiente (la migración, el cambio en `api.py`, la
infraestructura de Docker, el workflow de CI) y de validarlo contra una instancia real de
Postgres con los 100k posts sembrados: corriendo `EXPLAIN ANALYZE` antes/después, confirmando que
el trigger sobrevive a `bulk_create`, y probando los endpoints end-to-end.

# Arquitectura — 5 mejoras modo simple

> Fuente: `webapp/static/simple/index.html` + `webapp/app.py` (POST /api/jobs). Todo sin tocar `src/` ni `scm_agent/` — solo capa web + localStorage.

## Principios
- **Sin backend nuevo si no hace falta**: preview y tono son 100% frontend; historial/share usan storage ya existente (`_jobs_output`, `leads.jsonl`) + localStorage.
- **Degrada a batch**: si JS falla, el form POST clásico sigue funcionando.
- **Kern habla simple siempre**: cada feature traduce términos técnicos a criollo en el borde (UI), nunca en el engine.

---

## F1 — Preview Excel antes de ejecutar
**Problema**: usuario sube archivo y recién al fallar descubre que faltan columnas.

**Arquitectura**:
```
file input → FileReader (frontend, SheetJS-like via papaparse/openpyxl-js o fallback manual) 
  → parse 5 primeras filas → POST /api/preview (nuevo, read-only) 
  → detecta columnas vs REQUIRED_COLUMNS_HINT por tool inferido del brief
  → responde {columns:[], missing:[], rows:5, suggestions:[]}
  → UI muestra tabla preview + chips "✓ detectado / ✗ falta: quantity"
```
- **Backend**: `POST /api/preview` — reutiliza `jobs/<tool>_job.py:prepare()` en modo dry-run (no escribe deliverable). Solo valida, no ejecuta orquestador completo. Rate-limited, 5 MB cap.
- **Frontend**: papaparse para CSV (ya disponible), para XLSX usa `XLSX.read` lazy-import (si no está, muestra solo nombre/tamaño). Nunca sube archivo completo hasta "Ejecutar".
- **Fallback**: si no se puede parsear, muestra "No pude leerlo — probá CSV UTF-8 o marcá usar datos de ejemplo".

## F2 — Historial últimos 5 pedidos
**Arquitectura**:
```
localStorage: kern_simple_history = [{id, ts, brief, tool, status, summary, urls} x5]
POST /api/jobs success → push + trim 5 → render barra "Recientes" arriba del form
  → click = rehidrata brief + params + re-ejecuta o abre downloads
```
- Sin backend nuevo. El historial es del navegador (privado por usuario). `leads.jsonl` sigue siendo el historial server-side para operador.
- Límite 5 para no confundir. Cada item: `brief 40 chars … · hace 2h · ok`.

## F3 — Compartir por link (URL corta)
**Arquitectura**:
```
POST /api/jobs ok → job_dir = /jobs-output/<tmpid>/... 
  → frontend genera /share/<tmpid> (nuevo GET) que hace FileResponse del report.md renderizado
  → botón "Copiar link WhatsApp" → navigator.clipboard.writeText(origin + /share/<id>)
  → link expira con JOBS_TTL_SECONDS (3600s) — mismo TTL que jobs, sin storage nuevo
```
- **Backend**: `GET /share/{job_id}` — valida `job_id` es basename alfanumérico, resuelve dentro de `JOBS_OUTPUT_DIR`, sirve HTML renderizado del `report.md` (markdown→HTML). No expone filesystem fuera de jobs.
- Alternativa sin backend: `?share=<b64>` con datos inline — descartada (excede URL length).

## F4 — Tono Kern configurable
**Arquitectura**:
```
selector [Operario · Gerente · Auditor] → localStorage kern_tono
  → al renderizar kernSays + cards, elige plantilla de copy:
     operario: "Pedí 120u, te alcanza 14 días"
     gerente:  "Inversión $44k, ROI 12%, cobertura 95%"
     auditor:  "s=mu*L+z·σ·√L, z=1.64, validado por simulación"
  → el engine no cambia, solo el copy del frontend (misma data, distinta voz)
```
- 3 plantillas en `static/simple/tones.js` (objeto puro). Sin LLM. Si hay LLM, el tono se pasa como `params.tone` y `scm_agent/llm.py` lo usa para el summary — pero es opcional.
- Persistido en localStorage, aplica a futuros resultados sin re-ejecutar.

## F5 — Modo voz (dictado brief)
**Arquitectura**:
```
botón 🎤 → Web Speech API (webkitSpeechRecognition) → transcript → brief textarea
  → si no hay API (Firefox) → fallback: MediaRecorder → POST /api/transcribe (opcional, Whisper)
  → mientras graba, pulso rojo + "escuchando…"
```
- 100% frontend si hay SpeechRecognition. Sin backend obligatorio.
- Fase 2 opcional: `POST /api/transcribe` con `openai-whisper` local — solo si `WHISPER_ENABLED=1`, sino 501.
- Privacidad: nunca se sube audio sin explicit consent (el botón es el consent).

---

## Orden de construcción
1. F1 Preview (desbloquea todo lo demás)
2. F2 Historial (depende de F1 para re-ejecutar)
3. F3 Share link (depende de job_id de F1/F2)
4. F4 Tono (independiente, pero mejor con resultados reales de F1)
5. F5 Voz (independiente, última por ser enhancement)

## Archivos tocados
- `webapp/app.py`: + `POST /api/preview`, `GET /share/{id}`
- `webapp/static/simple/index.html`: cada F añade bloque JS/CSS aislado (feature-flag por `data-feature`)
- Ningún cambio en `src/`, `scm_agent/`, `jobs/` (solo import de `prepare` para preview).

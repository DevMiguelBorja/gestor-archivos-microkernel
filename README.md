# Gestor de archivos con arquitectura MicroKernel (Python)

Sistema de módulos independientes (procesos) que se comunican **solo a través del kernel** usando sockets TCP locales y mensajes JSON (uno por línea).

## Ejecución
```
python kernel.py            # lanza Gestor, Aplicaciones y la GUI (requiere tkinter)
python kernel.py --headless # sin GUI
python test_casos.py        # 15 casos de prueba automáticos
```
Linux: si falta tkinter -> `sudo apt install python3-tk`. Solo usa la biblioteca estándar.

## Módulos
| Archivo | Rol |
|---|---|
| `kernel.py` | Control: inicialización (lanza módulos y espera "listo"), ejecución (enruta `send`/`info`, `stop`, `launch`), finalización (envía `close` y termina procesos) |
| `gestor_archivos.py` | Crea/borra carpetas físicas en `workspace/` (`crear|nombre`, `borrar|nombre`) y mantiene `logs/transacciones.log` |
| `aplicaciones.py` | Proceso padre con 3 instancias hijas (APP1..APP3); cierra instancias, detecta cierres manuales, notifica al Gestor |
| `gui.py` | Estado en tiempo real, carpetas, aplicaciones, log y detener sistema |
| `common.py` | Protocolo, sorteo de estado (rangos) y estado Ocupado |

## Mensajes
- `{cmd:send, src:GUI, dst:GestorArc, msg:"crear|docs"}` · `{cmd:info, src:GUI, dst:APP, msg:"APP1"}` · `{cmd:stop, src:APP1, dst:kernel, msg:"..."}`
- Respuestas al kernel: `{codterm:0,msg:"OK"}`, `{codterm:1,msg:"0"}` (ocupado 1-3 s), `{codterm:2,msg:"Err"}`
- Sorteo (1..100): 1-70 procesado, 71-85 ocupado, 86-100 error (`common.RANGOS`).

## Decisiones de diseño
- Log de transacciones y refrescos automáticos de la GUI (`auto:true`) no participan del sorteo, para no perder registros ni parpadear la interfaz.
- Si el sorteo da Err, la operación **no** se ejecuta.
- Seguridad: nombres validados; solo se opera dentro de `workspace/` (sin `..` ni rutas absolutas).
- Variables para pruebas: `MK_PORT`, `MK_FORZAR=procesado|ocupado|err`, `MK_WORKSPACE`, `MK_LOGDIR`.

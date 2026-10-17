[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Español](README.es.md)

# Ollama Status Service

**Conoce el estado real de Ollama y da el siguiente turno a las solicitudes locales, sin interrumpir el trabajo en curso.**

**Versión 1.4.0 · Windows x64** · [Descargar](https://github.com/ozil56/OllamaStatusService/releases/tag/v1.4.0) · [Cambios](../CHANGELOG.md) · [Compilar](BUILD.md)

Una pasarela HTTP con servicios de Windows, instalador gráfico e icono de bandeja para una instalación existente de Ollama. Un modelo cargado no está necesariamente ocupado: la pasarela sigue las solicitudes que reenvía. No incluye Ollama, modelos ni controladores GPU. La interfaz está actualmente en chino simplificado; estas traducciones del README no traducen la aplicación.

La versión 1.4.0 integra la gestión de varios servidores en `/manage`: descarga y eliminación de modelos, pausa y reanudación de descargas. Utiliza la autenticación Nginx existente, sin otra cuenta. Las descargas continúan en el servidor de destino al cerrar el navegador.

La versión 1.2.5 corrige las listas de modelos vacías por falta de permisos de LocalService. La instalación y actualización conceden los permisos necesarios en la carpeta de modelos y verifican el acceso con la cuenta real del servicio y las listas de modelos. También se admite la carpeta de usuario predeterminada en C:.

La versión 1.2.4 corrige los tiempos de espera agotados al ejecutar varios `ollama pull`/`push` simultáneamente. Las transferencias omiten la cola y los límites de tiempo de inferencia, conservando el progreso en streaming. `/status` las cuenta por separado: indican `busy` para mantenimiento sin ocupar un puesto de inferencia. Usa `can_start_now` para comprobar si puede comenzar una inferencia.

## Funciones

- `/status` y `/dashboard`: estado, solicitudes activas y en cola, origen e identificadores.
- Prioridad para solicitudes locales en espera, FIFO dentro de cada clase. La tarea activa termina antes de ceder el turno; no se reservan puestos para lotes futuros.
- `/help`, descubrimiento del servicio y OpenAPI: capacidades, límites efectivos y reglas de integración.
- Bandeja: verde = libre, naranja = ocupado, rojo = desconectado, morado = finalización incierta. Cerrar el icono no detiene los servicios.
- Detección de rutas de modelos, verificación de integridad y comprobaciones antes de actualizar.

## Instalación y actualización

Requisitos: Windows 10/11 x64 o Windows Server con escritorio, Ollama y modelos locales existentes, PowerShell 5.1, .NET Framework 4.8, Programador de tareas, gestión del firewall y permisos de administrador para instalar.

1. Descarga `OllamaStatusService-Windows-x64-v1.4.0.zip` y `.sha256.txt` desde los archivos de la release. Compara con `Get-FileHash -Algorithm SHA256`. Los archivos automáticos Source code no son paquetes instalables.
2. Pausa las nuevas solicitudes, espera a que terminen las tareas activas y en cola y extrae todo el ZIP en una carpeta nueva.
3. Ejecuta **`安装.exe`**, comprueba las rutas de Ollama y modelos y acepta UAC. El mismo programa actualiza instalaciones existentes.
4. Abre `http://127.0.0.1:11434/dashboard`. El entorno y las dependencias están incluidos; la instalación no los descarga.

La ausencia de interrupción se aplica a la planificación normal. Instalar, actualizar, detener o recuperar servicios puede interrumpir tareas: usa una ventana de mantenimiento. Si falla la instalación nueva después de quitar la anterior, quedan errores y copias de seguridad, pero no se garantiza una reversión automática. **`卸载.exe`** desinstala el servicio conservando el Ollama original y sus modelos.

## Puertos y FRP

| Puerto | Función |
| --- | --- |
| `11434` | Acceso directo IPv4/IPv6, origen según el socket real |
| `11436` | Entrada proxy en `127.0.0.1` por defecto, siempre remote |
| `11435` | Backend Ollama en bucle local, no omitir la pasarela |

En cada servidor, cambia la entrada FRP existente a `localIP = "127.0.0.1"` y `localPort = 11436`. En INI: `local_ip` y `local_port`. Conserva nombres, puertos públicos, autenticación y TLS. Nginx/Caddy y túneles SSH del mismo equipo también deben usar `11436`. Un proxy en otro equipo necesita una dirección de escucha explícita y un firewall limitado a sus orígenes. No se interpreta PROXY protocol.

Ambas entradas comparten una cola. Las cabeceras HTTP no pueden obtener prioridad local. Comprueba `request_origin` en `/help`: `local` desde conexión local directa y `remote` mediante el proxy. Si el proxy sigue apuntando a `11434`, puede seguir clasificándose como local.

## Cola y límites de seguridad

`X-Ollama-Queue-Timeout` indica segundos máximos de espera: `0` rechaza inmediatamente cuando está ocupado; sin cabecera, el valor predeterminado es 15 segundos. Consulta `/help` para los límites activos. El plazo de espera no corta una inferencia ya iniciada. La prioridad local requiere entrar realmente en la cola; una carga local continua puede agotar el plazo de solicitudes remotas.

Cambiar automáticamente a otro servidor de forma segura requiere **HTTP 429/503 + `X-Ollama-Forwarded: false` + JSON `forwarded: false`**. Un tiempo de espera de red agotado, una desconexión o salida parcial no demuestran que no se ejecutó. `can_start_now` es una instantánea, no una reserva.

**La pasarela no añade autenticación.** Desde 1.2.2, la instalación/actualización permite TCP `11434` desde cualquier origen, dirección local y perfil de red, limitado al Python incluido. Las restricciones anteriores se guardan, pero no se conservan automáticamente. Revisa la regla y mantén autenticación, HTTPS y control de acceso de red. `11435` y `11436` usan bucle local por defecto.

Cuando no se puede confirmar la finalización, `unknown` bloquea nuevas tareas. La recuperación detiene y verifica el backend antes de borrar registros inciertos. No elimines la base de datos durante una ejecución. Las solicitudes en cola no se repiten tras reiniciar. Configuración: `%ProgramData%\OllamaStatusService`.

Las pruebas usan un backend simulado y no acreditan despliegues GPU/FRP en todos los equipos. Consulta el [README inglés](../README.md) para los detalles y el alcance de las pruebas.

## Licencia

No project source-code license has been selected yet. Publication does not grant a general license to use, modify or redistribute the project source. Third-party notices do not license this project’s own code.

Los componentes de terceros conservan sus [propias licencias](../licenses/THIRD-PARTY-NOTICES.txt).

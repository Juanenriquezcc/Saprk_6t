# Si algo falla durante el laboratorio

1. Ejecutar `run.cmd --check`.
2. Si dice `ENVIRONMENT READY`, ejecutar `run.cmd` (o `run.cmd datos.csv`).
3. Si falla el entorno virtual (`.venv`), ejecutar `run.cmd` de nuevo y aceptar la reparación.
   Si Spark sigue sin arrancar: `run.cmd --rebuild-venv`.
4. Si una pregunta no se entiende, reformularla mencionando la operación (promedio, suma, máximo,
   conteo) y la columna; o usar **9. Herramientas avanzadas → Análisis guiado**.
5. Si se necesita SQL, usar el menú **7. Ejecutar SQL manual** (vistas `dataset`, `dataset_original`, `rechazados`).
6. Si una regla de calidad tiene un error, el programa lo dice y deja corregirla (o pulsar Enter para seguir sin reglas).
7. Antes de cerrar, usar **8. Finalizar taller** para generar la evidencia en `exports\taller_...`.
8. Nunca instalar paquetes desde Internet.
9. Nunca modificar `JAVA_HOME` de forma permanente.
10. Nunca borrar la carpeta `tools\`.
11. Si el dataset no carga, revisar la ruta (se puede arrastrar el archivo a la terminal) y el formato (CSV, JSON o Parquet).

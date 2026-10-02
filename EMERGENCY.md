# Si algo falla durante el laboratorio

1. Ejecutar `run.cmd --check`.
2. Si dice `ENVIRONMENT READY`, ejecutar `run.cmd` (o `run.cmd datos.csv --lab`).
3. Si falla el entorno virtual (`.venv`), ejecutar `run.cmd` de nuevo y aceptar la reparación.
   Si Spark sigue sin arrancar: `run.cmd --rebuild-venv`.
4. Si una pregunta no se entiende, usar el menú **9. Analisis guiado**.
5. Si se necesita SQL, usar el menú **8. Ejecutar Spark SQL** (vista `dataset`).
6. Nunca instalar paquetes desde Internet.
7. Nunca modificar `JAVA_HOME` de forma permanente.
8. Nunca borrar la carpeta `tools\`.
9. Si el dataset no carga, revisar la ruta (se puede arrastrar el archivo a la terminal) y el formato (CSV, JSON o Parquet).

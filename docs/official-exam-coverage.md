# Cobertura de calendarios oficiales

La resolución estructurada de fechas solo publica resultados cuando puede verificar todos estos
enlaces oficiales: la página de la titulación, el plan de estudios del curso, el plan del
calendario del centro y una coincidencia exacta de código/título. Una coincidencia aproximada de
nombre nunca decide la titulación ni la fecha.

## Cualquier grado: planes de estudio y fichas de materia

Por defecto, `get_official_exam_dates` y `get_my_official_exam_schedule` funcionan para cualquier
grado de la USC sin configuración específica:

1. el código G se localiza en los planes oficiales del curso académico, primero en los grados del
   perfil (`degree_url` y `extra_degree_urls`) y, si no aparece, en todo el catálogo;
2. las fechas se leen de la tabla «Exames» de la ficha oficial de cada materia (`source_url`);
3. si el código está en varios planes, los grados del estudiante deciden; un plan sin fechas
   publicadas no contradice a otro que sí las tiene, y fechas distintas dan `ambiguous`.

La ficha no nombra la oportunidad (1ª o 2ª), así que `opportunity` queda vacío: el orden de las
fechas no se presenta como garantía. El primer barrido sin perfil puede tardar alrededor de un minuto
porque usc.gal es lento; planes y catálogo se guardan seis horas en caché y las consultas siguientes
tardan segundos. Con el grado en el perfil (`mcp-usc setup`) la primera consulta también es rápida.

## Crosswalk curado de calendarios de centro (opcional)

Con `degree_keys` se usa además el calendario de convocatorias del centro. Hoy están verificadas las
dos ediciones del doble grado en Enxeñaría Informática e Matemáticas, con calendarios de la ETSE y
de la Facultade de Matemáticas. Los identificadores de plan se mantienen separados porque las
ediciones homónimas no son intercambiables.

La USC publica páginas de centros y calendarios que no siempre contienen un identificador de plan
reutilizable ni un enlace directo al plan de estudios. Esos centros no se incorporan por inferencia:
se pueden consultar mediante `search_exam_dates` usando una URL USC configurada, conservando cada
enlace y el estado de la fuente. Para añadir un crosswalk estructurado hacen falta una URL canónica
de estudio, su endpoint de plan para el curso, el endpoint de calendario del centro, el identificador
de plan de ambos y una fixture reproducible; hasta entonces el resultado correcto es
`not_published_or_not_found` o `source_changed_or_unavailable`, no una fecha atribuida.

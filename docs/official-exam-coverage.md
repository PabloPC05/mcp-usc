# Cobertura de calendarios oficiales

La resolución estructurada de fechas solo publica resultados cuando puede verificar todos estos
enlaces oficiales: la página de la titulación, el plan de estudios del curso, el plan del
calendario del centro y una coincidencia exacta de código/título. Una coincidencia aproximada de
nombre nunca decide la titulación ni la fecha.

En esta versión están verificados dos ediciones del doble grado en Enxeñaría Informática e
Matemáticas, con calendarios de la ETSE y de la Facultade de Matemáticas. Los identificadores de
plan se mantienen separados porque las ediciones homónimas no son intercambiables.

La USC publica páginas de centros y calendarios que no siempre contienen un identificador de plan
reutilizable ni un enlace directo al plan de estudios. Esos centros no se incorporan por inferencia:
se pueden consultar mediante `search_exam_dates` usando una URL USC configurada, conservando cada
enlace y el estado de la fuente. Para añadir un crosswalk estructurado hacen falta una URL canónica
de estudio, su endpoint de plan para el curso, el endpoint de calendario del centro, el identificador
de plan de ambos y una fixture reproducible; hasta entonces el resultado correcto es
`not_published_or_not_found` o `source_changed_or_unavailable`, no una fecha atribuida.

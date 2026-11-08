# Novedades de ReNova

Cada viñeta de la versión actual aparece en el aviso de actualización dentro del programa
(tools/build.py la copia a `latest.json`). Escribilas para quien usa ReNova, no para quien lo programa.

## 2.4.5

- Convertir reconoce el tipo de archivo por su contenido y no por la extensión: una imagen con un nombre raro (por ejemplo «.0») ahora ofrece formatos de imagen, y un GIF animado sigue ofreciendo video.

## 2.4.4

- Zoom de la línea de tiempo con Alt + rueda del mouse, como en Premiere (en Recortar y en el recorte antes de descargar). La rueda sola o con Ctrl desplaza hacia los costados.
- Corregido: al soltar Alt se abría el menú de la ventana (Restaurar, Mover…) y la vista previa quedaba congelada.

## 2.4.3

- Recortar: la vista previa ya no se congela al hacer zoom mientras reproduce; si el video se traba, se recupera solo sin cortar el sonido.
- Ctrl + rueda del mouse desplaza la línea de tiempo hacia los costados (en Recortar y en el recorte antes de descargar).
- Recorte antes de descargar: la barra para desplazarte con zoom es más grande, se ve bien y se puede agarrar aunque el video sea muy largo.

## 2.4.2

- Recortar: el sonido se escucha completo al reproducir (antes se cortaba al empezar cada clip).
- Recortar: se quitaron los botones Entrada y Salida; el loop repite los clips elegidos.
- El contador de tiempo ya no hace temblar los controles mientras se reproduce.
- Recorte antes de descargar: con zoom las miniaturas ya no se estiran, y hay una barra abajo para moverte hacia los costados.

## 2.4.1

- Arrastrar archivos desde el Explorador a Convertir y a Recortar vuelve a funcionar (sobre la línea de tiempo se insertan en el corte más cercano).
- Control de volumen para escuchar mientras recortás, en Recortar y en el recorte antes de descargar. No cambia el volumen del archivo.

## 2.4.0

- Recortar ahora es una línea de tiempo como la de Premiere: cortá con la cuchilla (C) o Ctrl+K, mové, reordená y duplicá bloques, recortá los bordes y borrá partes dejando el hueco o cerrándolo.
- Podés unir varios videos y audios en la misma edición: arrastralos a la línea de tiempo.
- Imán: los bordes y las manijas se pegan al cabezal y a los cortes (también al recortar antes de descargar).
- Loop: repetí sólo la parte elegida mientras la revisás.
- Convertir detecta el formato de cada archivo y te muestra a qué lo podés pasar; el GIF tiene opciones de tamaño y fluidez.
- Corregido: convertir un video a GIF daba error.

## 2.3.0

- ReNova te avisa cuando sale una versión nueva y se actualiza con un clic, sin perder tus ajustes.
- Al instalar una versión nueva siempre se usa el motor de descargas más reciente.

## 2.2.0

- Indicador en la barra inferior que prueba si las descargas de YouTube funcionan y busca el arreglo solo.
- Pestaña Recortar: recortá videos o audios propios, quitá partes del medio y uní el resto.

## 2.1.0

- Reproductor con sonido para elegir el recorte con precisión antes de descargar.

## 2.0.0

- Rediseño completo, listas de reproducción con selección, recorte antes de descargar y videos
  siempre en H.264 + AAC compatibles con Premiere.

# Imagens do replay nativo

Estes PNGs vieram dos readbacks GPU verificados da DLL pós-correção `b051fe14b0965c7a45d3a2370d8dc68fc7a18e6ae8ba43ef4a5c4e0d7d745f9e`.

O replay usa a mesh, shaders e três conjuntos de câmera reais do EDF. Texturas quadriculadas, barras de cor e fundo raster são sintéticos. Não são screenshots do EDF nem prova de apresentação física.

- `native_x64_camera1_wsi_after.png`: imagem WSI completa, depois da composição GPU.
- `native_x64_camera*_panel.png`: painel GPU esperado nas três câmeras.

Os arquivos completos e comparações independentes estão em `runtime/_build/edf-composition-retained-x64/positive_camera*/`. Os quatro render targets originais permaneceram idênticos e nenhum pixel externo ao ROI mudou. A validação global Khronos permanece reprovada por erros também observados no baseline sem composição. Veja `../validation.json` e `../REPORT.md`.

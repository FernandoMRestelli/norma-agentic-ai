// Generado con `python -m internal.benchmark_manuscritos form` (semilla 116).
// No se edita a mano: se cambia el pool o la semilla y se vuelve a generar.
//
// 1. Pegalo en un proyecto nuevo de https://script.google.com y corré crearForm() con la cuenta que va a recibir las
//    fotos (pide permiso para crear Forms).
// 2. Abrí el link de edición que queda en el log y, en la sección final, agregá la pregunta «Subir archivo»: «Foto
//    del pedido», solo imágenes, un archivo, obligatoria. Apps Script no puede crear esa pregunta.
// 3. Mandá el link público con el número de lista de cada persona.

const INICIO = "¡Hola! Te pido una mano para un trabajo de la facu: estoy midiendo qué tan bien una IA lee pedidos escritos a mano.\n\n1. Elegí abajo el número de lista que te pasé y vas a ver tu lista.\n2. Copiala a mano en un papel, con tu letra de siempre, como si le hicieras un pedido a tu proveedor. Copiá las mismas palabras y números de cada renglón; mayúsculas, letra y espacios como te salgan.\n3. Arriba poné el nombre del negocio y la fecha que figuran en la lista (son inventados).\n4. Tachá con una línea el renglón que dice «TACHAR», como si te hubieras arrepentido.\n5. Sacale una foto con el celu como la mandarías por WhatsApp: sin escanear, sin recortar, con la luz que tengas, y subila al final.\n\nNo pongas tu nombre ni datos tuyos en la hoja. La foto se usa solo para este trabajo, no se publica, y se manda a la API del modelo que la lee (OpenAI o Google) para medir. Si preferís que no, no pasa nada.";
const CONSENTIMIENTO = "Acepto que la foto se use para este trabajo y se mande a la API del modelo que la lee";
const LISTAS = [
  {
    "id": "L01",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nSalón Los Tilos — 27/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nuna masc keratina Belkor 1 kg\nSilkara sh argan x900 — 2\nmedia docena Nordika ox 30 vol x90\nArgania ox 20 vol x900 — 1\nBelkor sh color 5L — 3\nTrevia ampolla reparadora x15 — 2 cajas\nArgania tintura 4.0 — 3\n2 Belkor crema de peinar rulos\nKapris coloración 6.1 con oxidante — 1\n3 cajas ampolla reparadora Kapris\n3 Montello pincel para tintura   ← TACHAR"
  },
  {
    "id": "L02",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería Los Álamos — 8/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\ntres serum puntas Lumine   ← TACHAR\nNordika coloración 7.0 con oxidante x 2\nBelkor ampolla reparadora 12 u\nOrkidea óleo argan x60 — cuatro\nBelkor oxidante 40 vol 900 x 3\n6 Silkara crema de peinar rulos\ncuatro Silkara sh matizador 300\nLumine sh nutrición 300 — 6\n4 protector térmico Lumine\nSilkara acond keratina 900 (2)\nBelkor decol sachet — 10"
  },
  {
    "id": "L03",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería El Ceibo — 2/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nTrevia acond nutrición 900 x 5\nOrkidea ampolla reparadora 20 u\ndos Fiorela máscara nutrición 250\n2 Fiorela serum puntas\nOrkidea máscara keratina x250 (3)\nOrkidea spray brillo x250 — 3\n2 cajas Silkara ampolla reparadora\n3 Lumine acond keratina 300\nOrkidea shampoo keratina 5 L x 1\nBelkor tintura 6.0 x2\n8 Orkidea gel fijador   ← TACHAR"
  },
  {
    "id": "L04",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nSalón Los Tilos — 11/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\n2 baño de crema karité Trevia 1 kg   ← TACHAR\n12 u Fiorela ampolla reparadora\n2 bowl para tintura Montello\n4 Belkor sh nutrición 300\nFiorela coloración 8.1 x 4\nBelkor protector térmico x250 — 1\n3 Kapris acond keratina 900\nmedia docena acond argan Orkidea 300\nDuomo coloración 9.1 (1)\nBrisa sh nutrición 900 (5)\n17 sachets decolorante Fiorela"
  },
  {
    "id": "L05",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nBarbería Don Tito — 22/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\n3 Belkor coloración 10.1\nKapris sh keratina 900 x 2\nOrkidea acond color 900 — 2\nTrevia masc nutrición 250 — 7\n1 baño de crema coco Trevia 1k\n11 Nordika oxidante 20 vol 90\nNordika masc argan 250 (6)   ← TACHAR\n2 cajas ampollas Nordika\nFiorela gel fijador x500 — 3\ndiez gel fijador Kapris 500\n7 Fiorela acond color 900"
  },
  {
    "id": "L06",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería Los Álamos — 15/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nTrevia shampoo matizador 900 (3)\n2 Nordika tintura 5.0\nKapris crema de peinar rulos (1)\n4 Belkor sh matizador 900\n2 acond keratina Nordika 900\n5 Kapris masc argan 1 kg\nMontello papel aluminio 5 kg x 1\n1 Kapris sh color 300\n5 Lumine sh nutrición 900   ← TACHAR\nKapris ox 10 vol x90 — doce\nLumine ampolla reparadora — 4 cajas"
  },
  {
    "id": "L07",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería La Esquina — 5/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nLumine crema de peinar rulos x300 — 4\n4 Brisa shampoo nutrición 5 L\n1 Silkara sh keratina 900   ← TACHAR\nKapris sh matizador 300 x 2\nuna docena spray brillo Silkara\nKapris coloración 6.1 con oxidante — 1\n2 Kapris óleo argan 60\n24 sachets decolorante Duomo\nLumine ampolla reparadora — 4 cajas\nArgania oxidante 10 vol x 90 — 7\nLumine acond argan 300 x 3"
  },
  {
    "id": "L08",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nEstética La Glorieta — 7/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\ndoce oxidante Belkor 20 vol x90\n3 Belkor coloración 10.1\n6 ox 40 vol Duomo x900\n2 cajas ampollas Nordika\nMontello peine de corte (10)\nFiorela sh matizador 5 L (1)\nNordika baño de crema karité 1k x 2   ← TACHAR\n10 serum puntas Orkidea\nLumine masc keratina x1k — 2\n5 Nordika protector térmico\n1 decol Belkor 700"
  },
  {
    "id": "L09",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nEstética Las Moras — 4/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\n2 Trevia sh argan 5L\nOrkidea ampolla reparadora 20 u\nuna docena spray brillo Silkara\nDuomo oxidante 30 vol 90 (17)\nSilkara masc nutrición 1k — 2\n1 Belkor baño de crema coco\nNordika oxidante 20 vol 900 x 4\nNordika spray brillo 250 x 3   ← TACHAR\nFiorela coloración 8.1 x 4\n1 Fiorela sh nutrición 5 L\nTrevia serum puntas x60 (11)"
  },
  {
    "id": "L10",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nEstudio Clara Luz — 5/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nBrisa acond color 900 x 2\n1 capa de corte Montello\nLumine crema de peinar rulos x300 — 4\n2 Nordika tintura 5.0\n1 Brisa sh matizador 5 L\n2 Orkidea sh color 900   ← TACHAR\ndos Fiorela máscara nutrición 250\nBelkor ampolla reparadora 12 u\nFiorela ox 30 vol 900 (2)\n3 Kapris acond color 300\nArgania polvo decolorante 700 g (2)"
  },
  {
    "id": "L11",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería La Esquina — 25/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\n1 capa de corte Montello\n6 Silkara crema de peinar rulos\nBelkor tintura 6.0 x2\n1 Belkor baño de crema coco\nTrevia serum puntas x60 (11)\nmedia docena Nordika ox 30 vol x90\nTrevia masc nutrición 250 — 7   ← TACHAR\nTrevia ampolla reparadora x15 — 2 cajas\nBelkor decol sachet — 10\n11 Nordika oxidante 20 vol 90\nOrkidea spray brillo x250 — 3"
  },
  {
    "id": "L12",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nSalón Arcoíris — 16/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nBelkor protector térmico x250 — 1   ← TACHAR\n12 u Fiorela ampolla reparadora\nDuomo coloración 9.1 (1)\nNordika baño de crema karité 1k x 2\nSilkara masc nutrición 1k — 2\nTrevia acond nutrición 900 x 5\n5 Lumine sh nutrición 900\ndoce oxidante Belkor 20 vol x90\n1 Fiorela sh nutrición 5 L\nNordika coloración 7.0 con oxidante x 2\nmedia docena acond argan Orkidea 300"
  },
  {
    "id": "L13",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería San Telmito — 6/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nLumine masc keratina x1k — 2\nArgania tintura 4.0 — 3\n2 bowl para tintura Montello\nOrkidea óleo argan x60 — cuatro\n1 Brisa sh matizador 5 L\nOrkidea máscara keratina x250 (3)   ← TACHAR\ndiez gel fijador Kapris 500\nSilkara sh argan x900 — 2\nNordika masc argan 250 (6)\n2 cajas Silkara ampolla reparadora\n1 Silkara sh keratina 900"
  },
  {
    "id": "L14",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería El Ceibo — 15/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nBelkor sh color 5L — 3\n2 baño de crema karité Trevia 1 kg   ← TACHAR\nBelkor oxidante 40 vol 900 x 3\nOrkidea shampoo keratina 5 L x 1\nNordika oxidante 20 vol 900 x 4\n3 Kapris acond color 300\nBelkor tintura 6.0 x2\nFiorela gel fijador x500 — 3\nuna masc keratina Belkor 1 kg\n3 cajas ampolla reparadora Kapris\n3 Kapris acond keratina 900"
  },
  {
    "id": "L15",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería La Esquina — 9/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nDuomo coloración 9.1 (1)\nSilkara acond keratina 900 (2)\n2 cajas ampollas Nordika\nArgania polvo decolorante 700 g (2)\nFiorela ox 30 vol 900 (2)\nKapris ox 10 vol x90 — doce\n4 Brisa shampoo nutrición 5 L\n2 acond keratina Nordika 900\n6 ox 40 vol Duomo x900   ← TACHAR\nMontello papel aluminio 5 kg x 1\n4 protector térmico Lumine"
  },
  {
    "id": "L16",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería San Telmito — 25/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\n24 sachets decolorante Duomo\nKapris crema de peinar rulos (1)\n3 Montello pincel para tintura   ← TACHAR\n2 Kapris óleo argan 60\nArgania ox 20 vol x900 — 1\n10 serum puntas Orkidea\nTrevia ampolla reparadora x15 — 2 cajas\n17 sachets decolorante Fiorela\n1 decol Belkor 700\ncuatro Silkara sh matizador 300\nArgania tintura 4.0 — 3"
  },
  {
    "id": "L17",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nEstética Vía Bella — 6/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nBelkor ampolla reparadora 12 u\n2 Fiorela serum puntas\n3 Lumine acond keratina 300\ntres serum puntas Lumine\nArgania oxidante 10 vol x 90 — 7\n5 Nordika protector térmico\nKapris sh keratina 900 x 2\nLumine sh nutrición 300 — 6   ← TACHAR\nDuomo oxidante 30 vol 90 (17)\n2 Trevia sh argan 5L\nKapris coloración 6.1 con oxidante — 1"
  },
  {
    "id": "L18",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nEstética Vía Bella — 18/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nTrevia shampoo matizador 900 (3)\nKapris sh matizador 300 x 2\nFiorela sh matizador 5 L (1)\ntres serum puntas Lumine\nLumine ampolla reparadora — 4 cajas\n7 Fiorela acond color 900\nmedia docena Nordika ox 30 vol x90\n1 baño de crema coco Trevia 1k\n5 Kapris masc argan 1 kg   ← TACHAR\n2 Nordika tintura 5.0\n4 Belkor sh matizador 900"
  },
  {
    "id": "L19",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nSalón Los Tilos — 5/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\ndiez gel fijador Kapris 500\nBrisa acond color 900 x 2\nKapris ox 10 vol x90 — doce\n2 Belkor crema de peinar rulos   ← TACHAR\n1 Kapris sh color 300\nBrisa sh nutrición 900 (5)\nOrkidea ampolla reparadora 20 u\nMontello peine de corte (10)\n3 Belkor coloración 10.1\nNordika spray brillo 250 x 3\n2 Orkidea sh color 900"
  },
  {
    "id": "L20",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nBarbería Don Tito — 10/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nOrkidea óleo argan x60 — cuatro\nArgania polvo decolorante 700 g (2)   ← TACHAR\n4 Belkor sh nutrición 300\n8 Orkidea gel fijador\nOrkidea acond color 900 — 2\nLumine acond argan 300 x 3\nFiorela coloración 8.1 x 4\nArgania oxidante 10 vol x 90 — 7\n1 baño de crema coco Trevia 1k\n12 u Fiorela ampolla reparadora\n3 cajas ampolla reparadora Kapris"
  },
  {
    "id": "L21",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nEstética Las Moras — 11/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nBrisa sh nutrición 900 (5)\nNordika coloración 7.0 con oxidante x 2\nArgania ox 20 vol x900 — 1\n6 Silkara crema de peinar rulos\nOrkidea acond color 900 — 2\ncuatro Silkara sh matizador 300\nOrkidea spray brillo x250 — 3\nMontello papel aluminio 5 kg x 1\nNordika oxidante 20 vol 900 x 4\nBrisa acond color 900 x 2   ← TACHAR\n2 cajas Silkara ampolla reparadora"
  },
  {
    "id": "L22",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nBarbería Don Tito — 28/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nmedia docena acond argan Orkidea 300\nFiorela gel fijador x500 — 3\n3 Kapris acond color 300\nKapris crema de peinar rulos (1)\n2 acond keratina Nordika 900   ← TACHAR\nOrkidea máscara keratina x250 (3)\nuna masc keratina Belkor 1 kg\nFiorela sh matizador 5 L (1)\nSilkara acond keratina 900 (2)\n3 cajas ampolla reparadora Kapris\nKapris coloración 6.1 con oxidante — 1"
  },
  {
    "id": "L23",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nSalón Margarita — 10/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nKapris sh matizador 300 x 2\n3 Lumine acond keratina 300\nTrevia serum puntas x60 (11)   ← TACHAR\nMontello peine de corte (10)\n1 Brisa sh matizador 5 L\n2 Trevia sh argan 5L\nTrevia ampolla reparadora x15 — 2 cajas\nBelkor tintura 6.0 x2\nBelkor protector térmico x250 — 1\ndoce oxidante Belkor 20 vol x90\ndos Fiorela máscara nutrición 250"
  },
  {
    "id": "L24",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nSalón Los Tilos — 1/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nLumine crema de peinar rulos x300 — 4   ← TACHAR\n24 sachets decolorante Duomo\nSilkara sh argan x900 — 2\nDuomo coloración 9.1 (1)\nNordika masc argan 250 (6)\n2 cajas ampollas Nordika\n11 Nordika oxidante 20 vol 90\n7 Fiorela acond color 900\n2 Orkidea sh color 900\nuna docena spray brillo Silkara\nSilkara masc nutrición 1k — 2"
  },
  {
    "id": "L25",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nSalón Los Tilos — 14/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\n10 serum puntas Orkidea   ← TACHAR\nBelkor oxidante 40 vol 900 x 3\nuna masc keratina Belkor 1 kg\nKapris sh keratina 900 x 2\n4 Brisa shampoo nutrición 5 L\n3 Belkor coloración 10.1\n2 cajas Silkara ampolla reparadora\n3 Montello pincel para tintura\nTrevia shampoo matizador 900 (3)\n6 ox 40 vol Duomo x900\n1 capa de corte Montello"
  },
  {
    "id": "L26",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nEstética Las Moras — 27/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nLumine sh nutrición 300 — 6\nTrevia acond nutrición 900 x 5\nKapris ox 10 vol x90 — doce\n1 decol Belkor 700\nBelkor decol sachet — 10\n4 protector térmico Lumine\nArgania tintura 4.0 — 3\nLumine acond argan 300 x 3\n1 Silkara sh keratina 900   ← TACHAR\n12 u Fiorela ampolla reparadora\n4 Belkor sh nutrición 300"
  },
  {
    "id": "L27",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nSalón Margarita — 14/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\ndiez gel fijador Kapris 500\n2 Belkor crema de peinar rulos   ← TACHAR\n5 Lumine sh nutrición 900\n5 Kapris masc argan 1 kg\n2 Fiorela serum puntas\n4 Belkor sh matizador 900\n1 Kapris sh color 300\nBelkor ampolla reparadora 12 u\n8 Orkidea gel fijador\n2 Nordika tintura 5.0\nLumine masc keratina x1k — 2"
  },
  {
    "id": "L28",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nEstética Las Moras — 27/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nNordika coloración 7.0 con oxidante x 2\nOrkidea ampolla reparadora 20 u\ndos Fiorela máscara nutrición 250\nBelkor sh color 5L — 3\n3 Kapris acond keratina 900\nTrevia masc nutrición 250 — 7   ← TACHAR\nDuomo oxidante 30 vol 90 (17)\nNordika baño de crema karité 1k x 2\nOrkidea shampoo keratina 5 L x 1\nNordika spray brillo 250 x 3\nBelkor protector térmico x250 — 1"
  },
  {
    "id": "L29",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nPeluquería Los Álamos — 1/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\n2 baño de crema karité Trevia 1 kg\nFiorela coloración 8.1 x 4\n5 Nordika protector térmico\n2 Kapris óleo argan 60\nmedia docena Nordika ox 30 vol x90\nFiorela ox 30 vol 900 (2)\n2 bowl para tintura Montello   ← TACHAR\n1 Fiorela sh nutrición 5 L\nLumine ampolla reparadora — 4 cajas\n1 Belkor baño de crema coco\n17 sachets decolorante Fiorela"
  },
  {
    "id": "L30",
    "texto": "Copiá esta lista en un papel, con el encabezado arriba.\n\nEstética Las Moras — 26/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 — (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector térmico (7)\nSilkara acond keratina 900 (2)\n6 ox 40 vol Duomo x900\n4 Belkor sh nutrición 300   ← TACHAR\nArgania ox 20 vol x900 — 1\nBrisa acond color 900 x 2\nSilkara sh argan x900 — 2\n1 Belkor baño de crema coco\nBelkor tintura 6.0 x2\nOrkidea óleo argan x60 — cuatro\nKapris sh keratina 900 x 2\n2 cajas ampollas Nordika"
  }
];

function crearForm() {
  const form = FormApp.create('Pedidos escritos a mano');
  form.setDescription(INICIO);
  form.setCollectEmail(false);
  form.setProgressBar(true);

  form.addCheckboxItem()
    .setTitle('Consentimiento')
    .setChoiceValues([CONSENTIMIENTO])
    .setRequired(true);
  const elegir = form.addListItem()
    .setTitle('¿Qué número de lista te tocó?')
    .setRequired(true);

  const secciones = LISTAS.map(function (lista) {
    return form.addPageBreakItem()
      .setTitle('Lista ' + lista.id)
      .setHelpText(lista.texto);
  });

  const final = form.addPageBreakItem()
    .setTitle('La foto y unas preguntas')
    .setHelpText('Subí la foto de la hoja y contanos cómo la sacaste.');
  secciones.forEach(function (seccion) {
    seccion.setGoToPage(final);
  });
  elegir.setChoices(LISTAS.map(function (lista, i) {
    return elegir.createChoice(lista.id, secciones[i]);
  }));

  form.addMultipleChoiceItem()
    .setTitle('¿Copiaste todos los renglones tal cual?')
    .setChoiceValues(['Sí', 'No'])
    .setRequired(true);
  form.addParagraphTextItem()
    .setTitle('Si no, ¿cuáles cambiaste o salteaste?');
  form.addMultipleChoiceItem()
    .setTitle('Luz')
    .setChoiceValues(['Natural', 'Artificial', 'Poca luz'])
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('Papel')
    .setChoiceValues(['Cuaderno rayado', 'Hoja lisa', 'Otro'])
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('Ángulo de la foto')
    .setChoiceValues(['De frente', 'De costado'])
    .setRequired(true);

  Logger.log('Para editar (agregá la pregunta de la foto): ' + form.getEditUrl());
  Logger.log('Para mandar: ' + form.getPublishedUrl());
}

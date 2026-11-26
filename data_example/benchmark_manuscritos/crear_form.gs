// Generado con `python -m internal.benchmark_manuscritos form` (semilla 116).
// No se edita a mano: se cambia el pool o la semilla y se vuelve a generar.
//
// 1. Pegalo en un proyecto nuevo de https://script.google.com y corre crearForm() con la cuenta que va a recibir
//    las fotos (pide permiso para crear Forms).
// 2. Abri el link de edicion que queda en el log y, en la seccion final, agrega la pregunta "Subir archivo":
//    "Foto del pedido", solo imagenes, un archivo, obligatoria. Apps Script no puede crear esa pregunta.
// 3. Manda el link publico con el numero de lista de cada persona.

const TEXTOS = {
  "titulo": "Pedidos escritos a mano",
  "inicio": "\u00a1Hola! Te pido una mano para un trabajo de la facu: estoy midiendo qu\u00e9 tan bien una IA lee pedidos escritos a mano.\n\n1. Eleg\u00ed abajo el n\u00famero de lista que te pas\u00e9 y vas a ver tu lista.\n2. Copiala a mano en un papel, con tu letra de siempre, como si le hicieras un pedido a tu proveedor. Copi\u00e1 las mismas palabras y n\u00fameros de cada rengl\u00f3n; may\u00fasculas, letra y espacios como te salgan.\n3. Arriba pon\u00e9 el nombre del negocio y la fecha que figuran en la lista (son inventados).\n4. Tach\u00e1 con una l\u00ednea el rengl\u00f3n que dice \u00abTACHAR\u00bb, como si te hubieras arrepentido.\n5. Sacale una foto con el celu como la mandar\u00edas por WhatsApp: sin escanear, sin recortar, con la luz que tengas, y subila al final.\n\nNo pongas tu nombre ni datos tuyos en la hoja. La foto se usa solo para este trabajo, no se publica, y se manda a la API del modelo que la lee (OpenAI o Google) para medir. Si prefer\u00eds que no, no pasa nada.",
  "consentimiento_titulo": "Consentimiento",
  "consentimiento": "Acepto que la foto se use para este trabajo y se mande a la API del modelo que la lee",
  "elegir_lista": "\u00bfQu\u00e9 n\u00famero de lista te toc\u00f3?",
  "lista": "Lista ",
  "final_titulo": "La foto y unas preguntas",
  "final_ayuda": "Sub\u00ed la foto de la hoja y contanos c\u00f3mo la sacaste.",
  "copio_todo": "\u00bfCopiaste todos los renglones tal cual?",
  "si_no": [
    "S\u00ed",
    "No"
  ],
  "cambios": "Si no, \u00bfcu\u00e1les cambiaste o salteaste?",
  "luz": "Luz",
  "luces": [
    "Natural",
    "Artificial",
    "Poca luz"
  ],
  "papel": "Papel",
  "papeles": [
    "Cuaderno rayado",
    "Hoja lisa",
    "Otro"
  ],
  "angulo": "\u00c1ngulo de la foto",
  "angulos": [
    "De frente",
    "De costado"
  ]
};
const LISTAS = [
  {
    "id": "L01",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nSal\u00f3n Los Tilos \u2014 27/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nuna masc keratina Belkor 1 kg\nSilkara sh argan x900 \u2014 2\nmedia docena Nordika ox 30 vol x90\nArgania ox 20 vol x900 \u2014 1\nBelkor sh color 5L \u2014 3\nTrevia ampolla reparadora x15 \u2014 2 cajas\nArgania tintura 4.0 \u2014 3\n2 Belkor crema de peinar rulos\nKapris coloraci\u00f3n 6.1 con oxidante \u2014 1\n3 cajas ampolla reparadora Kapris\n3 Montello pincel para tintura   \u2190 TACHAR"
  },
  {
    "id": "L02",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda Los \u00c1lamos \u2014 8/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\ntres serum puntas Lumine   \u2190 TACHAR\nNordika coloraci\u00f3n 7.0 con oxidante x 2\nBelkor ampolla reparadora 12 u\nOrkidea \u00f3leo argan x60 \u2014 cuatro\nBelkor oxidante 40 vol 900 x 3\n6 Silkara crema de peinar rulos\ncuatro Silkara sh matizador 300\nLumine sh nutrici\u00f3n 300 \u2014 6\n4 protector t\u00e9rmico Lumine\nSilkara acond keratina 900 (2)\nBelkor decol sachet \u2014 10"
  },
  {
    "id": "L03",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda El Ceibo \u2014 2/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nTrevia acond nutrici\u00f3n 900 x 5\nOrkidea ampolla reparadora 20 u\ndos Fiorela m\u00e1scara nutrici\u00f3n 250\n2 Fiorela serum puntas\nOrkidea m\u00e1scara keratina x250 (3)\nOrkidea spray brillo x250 \u2014 3\n2 cajas Silkara ampolla reparadora\n3 Lumine acond keratina 300\nOrkidea shampoo keratina 5 L x 1\nBelkor tintura 6.0 x2\n8 Orkidea gel fijador   \u2190 TACHAR"
  },
  {
    "id": "L04",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nSal\u00f3n Los Tilos \u2014 11/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n2 ba\u00f1o de crema karit\u00e9 Trevia 1 kg   \u2190 TACHAR\n12 u Fiorela ampolla reparadora\n2 bowl para tintura Montello\n4 Belkor sh nutrici\u00f3n 300\nFiorela coloraci\u00f3n 8.1 x 4\nBelkor protector t\u00e9rmico x250 \u2014 1\n3 Kapris acond keratina 900\nmedia docena acond argan Orkidea 300\nDuomo coloraci\u00f3n 9.1 (1)\nBrisa sh nutrici\u00f3n 900 (5)\n17 sachets decolorante Fiorela"
  },
  {
    "id": "L05",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nBarber\u00eda Don Tito \u2014 22/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n3 Belkor coloraci\u00f3n 10.1\nKapris sh keratina 900 x 2\nOrkidea acond color 900 \u2014 2\nTrevia masc nutrici\u00f3n 250 \u2014 7\n1 ba\u00f1o de crema coco Trevia 1k\n11 Nordika oxidante 20 vol 90\nNordika masc argan 250 (6)   \u2190 TACHAR\n2 cajas ampollas Nordika\nFiorela gel fijador x500 \u2014 3\ndiez gel fijador Kapris 500\n7 Fiorela acond color 900"
  },
  {
    "id": "L06",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda Los \u00c1lamos \u2014 15/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nTrevia shampoo matizador 900 (3)\n2 Nordika tintura 5.0\nKapris crema de peinar rulos (1)\n4 Belkor sh matizador 900\n2 acond keratina Nordika 900\n5 Kapris masc argan 1 kg\nMontello papel aluminio 5 kg x 1\n1 Kapris sh color 300\n5 Lumine sh nutrici\u00f3n 900   \u2190 TACHAR\nKapris ox 10 vol x90 \u2014 doce\nLumine ampolla reparadora \u2014 4 cajas"
  },
  {
    "id": "L07",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda La Esquina \u2014 5/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nLumine crema de peinar rulos x300 \u2014 4\n4 Brisa shampoo nutrici\u00f3n 5 L\n1 Silkara sh keratina 900   \u2190 TACHAR\nKapris sh matizador 300 x 2\nuna docena spray brillo Silkara\nKapris coloraci\u00f3n 6.1 con oxidante \u2014 1\n2 Kapris \u00f3leo argan 60\n24 sachets decolorante Duomo\nLumine ampolla reparadora \u2014 4 cajas\nArgania oxidante 10 vol x 90 \u2014 7\nLumine acond argan 300 x 3"
  },
  {
    "id": "L08",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nEst\u00e9tica La Glorieta \u2014 7/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\ndoce oxidante Belkor 20 vol x90\n3 Belkor coloraci\u00f3n 10.1\n6 ox 40 vol Duomo x900\n2 cajas ampollas Nordika\nMontello peine de corte (10)\nFiorela sh matizador 5 L (1)\nNordika ba\u00f1o de crema karit\u00e9 1k x 2   \u2190 TACHAR\n10 serum puntas Orkidea\nLumine masc keratina x1k \u2014 2\n5 Nordika protector t\u00e9rmico\n1 decol Belkor 700"
  },
  {
    "id": "L09",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nEst\u00e9tica Las Moras \u2014 4/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n2 Trevia sh argan 5L\nOrkidea ampolla reparadora 20 u\nuna docena spray brillo Silkara\nDuomo oxidante 30 vol 90 (17)\nSilkara masc nutrici\u00f3n 1k \u2014 2\n1 Belkor ba\u00f1o de crema coco\nNordika oxidante 20 vol 900 x 4\nNordika spray brillo 250 x 3   \u2190 TACHAR\nFiorela coloraci\u00f3n 8.1 x 4\n1 Fiorela sh nutrici\u00f3n 5 L\nTrevia serum puntas x60 (11)"
  },
  {
    "id": "L10",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nEstudio Clara Luz \u2014 5/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nBrisa acond color 900 x 2\n1 capa de corte Montello\nLumine crema de peinar rulos x300 \u2014 4\n2 Nordika tintura 5.0\n1 Brisa sh matizador 5 L\n2 Orkidea sh color 900   \u2190 TACHAR\ndos Fiorela m\u00e1scara nutrici\u00f3n 250\nBelkor ampolla reparadora 12 u\nFiorela ox 30 vol 900 (2)\n3 Kapris acond color 300\nArgania polvo decolorante 700 g (2)"
  },
  {
    "id": "L11",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda La Esquina \u2014 25/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n1 capa de corte Montello\n6 Silkara crema de peinar rulos\nBelkor tintura 6.0 x2\n1 Belkor ba\u00f1o de crema coco\nTrevia serum puntas x60 (11)\nmedia docena Nordika ox 30 vol x90\nTrevia masc nutrici\u00f3n 250 \u2014 7   \u2190 TACHAR\nTrevia ampolla reparadora x15 \u2014 2 cajas\nBelkor decol sachet \u2014 10\n11 Nordika oxidante 20 vol 90\nOrkidea spray brillo x250 \u2014 3"
  },
  {
    "id": "L12",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nSal\u00f3n Arco\u00edris \u2014 16/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nBelkor protector t\u00e9rmico x250 \u2014 1   \u2190 TACHAR\n12 u Fiorela ampolla reparadora\nDuomo coloraci\u00f3n 9.1 (1)\nNordika ba\u00f1o de crema karit\u00e9 1k x 2\nSilkara masc nutrici\u00f3n 1k \u2014 2\nTrevia acond nutrici\u00f3n 900 x 5\n5 Lumine sh nutrici\u00f3n 900\ndoce oxidante Belkor 20 vol x90\n1 Fiorela sh nutrici\u00f3n 5 L\nNordika coloraci\u00f3n 7.0 con oxidante x 2\nmedia docena acond argan Orkidea 300"
  },
  {
    "id": "L13",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda San Telmito \u2014 6/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nLumine masc keratina x1k \u2014 2\nArgania tintura 4.0 \u2014 3\n2 bowl para tintura Montello\nOrkidea \u00f3leo argan x60 \u2014 cuatro\n1 Brisa sh matizador 5 L\nOrkidea m\u00e1scara keratina x250 (3)   \u2190 TACHAR\ndiez gel fijador Kapris 500\nSilkara sh argan x900 \u2014 2\nNordika masc argan 250 (6)\n2 cajas Silkara ampolla reparadora\n1 Silkara sh keratina 900"
  },
  {
    "id": "L14",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda El Ceibo \u2014 15/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nBelkor sh color 5L \u2014 3\n2 ba\u00f1o de crema karit\u00e9 Trevia 1 kg   \u2190 TACHAR\nBelkor oxidante 40 vol 900 x 3\nOrkidea shampoo keratina 5 L x 1\nNordika oxidante 20 vol 900 x 4\n3 Kapris acond color 300\nBelkor tintura 6.0 x2\nFiorela gel fijador x500 \u2014 3\nuna masc keratina Belkor 1 kg\n3 cajas ampolla reparadora Kapris\n3 Kapris acond keratina 900"
  },
  {
    "id": "L15",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda La Esquina \u2014 9/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nDuomo coloraci\u00f3n 9.1 (1)\nSilkara acond keratina 900 (2)\n2 cajas ampollas Nordika\nArgania polvo decolorante 700 g (2)\nFiorela ox 30 vol 900 (2)\nKapris ox 10 vol x90 \u2014 doce\n4 Brisa shampoo nutrici\u00f3n 5 L\n2 acond keratina Nordika 900\n6 ox 40 vol Duomo x900   \u2190 TACHAR\nMontello papel aluminio 5 kg x 1\n4 protector t\u00e9rmico Lumine"
  },
  {
    "id": "L16",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda San Telmito \u2014 25/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n24 sachets decolorante Duomo\nKapris crema de peinar rulos (1)\n3 Montello pincel para tintura   \u2190 TACHAR\n2 Kapris \u00f3leo argan 60\nArgania ox 20 vol x900 \u2014 1\n10 serum puntas Orkidea\nTrevia ampolla reparadora x15 \u2014 2 cajas\n17 sachets decolorante Fiorela\n1 decol Belkor 700\ncuatro Silkara sh matizador 300\nArgania tintura 4.0 \u2014 3"
  },
  {
    "id": "L17",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nEst\u00e9tica V\u00eda Bella \u2014 6/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nBelkor ampolla reparadora 12 u\n2 Fiorela serum puntas\n3 Lumine acond keratina 300\ntres serum puntas Lumine\nArgania oxidante 10 vol x 90 \u2014 7\n5 Nordika protector t\u00e9rmico\nKapris sh keratina 900 x 2\nLumine sh nutrici\u00f3n 300 \u2014 6   \u2190 TACHAR\nDuomo oxidante 30 vol 90 (17)\n2 Trevia sh argan 5L\nKapris coloraci\u00f3n 6.1 con oxidante \u2014 1"
  },
  {
    "id": "L18",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nEst\u00e9tica V\u00eda Bella \u2014 18/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nTrevia shampoo matizador 900 (3)\nKapris sh matizador 300 x 2\nFiorela sh matizador 5 L (1)\ntres serum puntas Lumine\nLumine ampolla reparadora \u2014 4 cajas\n7 Fiorela acond color 900\nmedia docena Nordika ox 30 vol x90\n1 ba\u00f1o de crema coco Trevia 1k\n5 Kapris masc argan 1 kg   \u2190 TACHAR\n2 Nordika tintura 5.0\n4 Belkor sh matizador 900"
  },
  {
    "id": "L19",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nSal\u00f3n Los Tilos \u2014 5/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\ndiez gel fijador Kapris 500\nBrisa acond color 900 x 2\nKapris ox 10 vol x90 \u2014 doce\n2 Belkor crema de peinar rulos   \u2190 TACHAR\n1 Kapris sh color 300\nBrisa sh nutrici\u00f3n 900 (5)\nOrkidea ampolla reparadora 20 u\nMontello peine de corte (10)\n3 Belkor coloraci\u00f3n 10.1\nNordika spray brillo 250 x 3\n2 Orkidea sh color 900"
  },
  {
    "id": "L20",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nBarber\u00eda Don Tito \u2014 10/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nOrkidea \u00f3leo argan x60 \u2014 cuatro\nArgania polvo decolorante 700 g (2)   \u2190 TACHAR\n4 Belkor sh nutrici\u00f3n 300\n8 Orkidea gel fijador\nOrkidea acond color 900 \u2014 2\nLumine acond argan 300 x 3\nFiorela coloraci\u00f3n 8.1 x 4\nArgania oxidante 10 vol x 90 \u2014 7\n1 ba\u00f1o de crema coco Trevia 1k\n12 u Fiorela ampolla reparadora\n3 cajas ampolla reparadora Kapris"
  },
  {
    "id": "L21",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nEst\u00e9tica Las Moras \u2014 11/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nBrisa sh nutrici\u00f3n 900 (5)\nNordika coloraci\u00f3n 7.0 con oxidante x 2\nArgania ox 20 vol x900 \u2014 1\n6 Silkara crema de peinar rulos\nOrkidea acond color 900 \u2014 2\ncuatro Silkara sh matizador 300\nOrkidea spray brillo x250 \u2014 3\nMontello papel aluminio 5 kg x 1\nNordika oxidante 20 vol 900 x 4\nBrisa acond color 900 x 2   \u2190 TACHAR\n2 cajas Silkara ampolla reparadora"
  },
  {
    "id": "L22",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nBarber\u00eda Don Tito \u2014 28/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nmedia docena acond argan Orkidea 300\nFiorela gel fijador x500 \u2014 3\n3 Kapris acond color 300\nKapris crema de peinar rulos (1)\n2 acond keratina Nordika 900   \u2190 TACHAR\nOrkidea m\u00e1scara keratina x250 (3)\nuna masc keratina Belkor 1 kg\nFiorela sh matizador 5 L (1)\nSilkara acond keratina 900 (2)\n3 cajas ampolla reparadora Kapris\nKapris coloraci\u00f3n 6.1 con oxidante \u2014 1"
  },
  {
    "id": "L23",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nSal\u00f3n Margarita \u2014 10/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nKapris sh matizador 300 x 2\n3 Lumine acond keratina 300\nTrevia serum puntas x60 (11)   \u2190 TACHAR\nMontello peine de corte (10)\n1 Brisa sh matizador 5 L\n2 Trevia sh argan 5L\nTrevia ampolla reparadora x15 \u2014 2 cajas\nBelkor tintura 6.0 x2\nBelkor protector t\u00e9rmico x250 \u2014 1\ndoce oxidante Belkor 20 vol x90\ndos Fiorela m\u00e1scara nutrici\u00f3n 250"
  },
  {
    "id": "L24",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nSal\u00f3n Los Tilos \u2014 1/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nLumine crema de peinar rulos x300 \u2014 4   \u2190 TACHAR\n24 sachets decolorante Duomo\nSilkara sh argan x900 \u2014 2\nDuomo coloraci\u00f3n 9.1 (1)\nNordika masc argan 250 (6)\n2 cajas ampollas Nordika\n11 Nordika oxidante 20 vol 90\n7 Fiorela acond color 900\n2 Orkidea sh color 900\nuna docena spray brillo Silkara\nSilkara masc nutrici\u00f3n 1k \u2014 2"
  },
  {
    "id": "L25",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nSal\u00f3n Los Tilos \u2014 14/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n10 serum puntas Orkidea   \u2190 TACHAR\nBelkor oxidante 40 vol 900 x 3\nuna masc keratina Belkor 1 kg\nKapris sh keratina 900 x 2\n4 Brisa shampoo nutrici\u00f3n 5 L\n3 Belkor coloraci\u00f3n 10.1\n2 cajas Silkara ampolla reparadora\n3 Montello pincel para tintura\nTrevia shampoo matizador 900 (3)\n6 ox 40 vol Duomo x900\n1 capa de corte Montello"
  },
  {
    "id": "L26",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nEst\u00e9tica Las Moras \u2014 27/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nLumine sh nutrici\u00f3n 300 \u2014 6\nTrevia acond nutrici\u00f3n 900 x 5\nKapris ox 10 vol x90 \u2014 doce\n1 decol Belkor 700\nBelkor decol sachet \u2014 10\n4 protector t\u00e9rmico Lumine\nArgania tintura 4.0 \u2014 3\nLumine acond argan 300 x 3\n1 Silkara sh keratina 900   \u2190 TACHAR\n12 u Fiorela ampolla reparadora\n4 Belkor sh nutrici\u00f3n 300"
  },
  {
    "id": "L27",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nSal\u00f3n Margarita \u2014 14/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\ndiez gel fijador Kapris 500\n2 Belkor crema de peinar rulos   \u2190 TACHAR\n5 Lumine sh nutrici\u00f3n 900\n5 Kapris masc argan 1 kg\n2 Fiorela serum puntas\n4 Belkor sh matizador 900\n1 Kapris sh color 300\nBelkor ampolla reparadora 12 u\n8 Orkidea gel fijador\n2 Nordika tintura 5.0\nLumine masc keratina x1k \u2014 2"
  },
  {
    "id": "L28",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nEst\u00e9tica Las Moras \u2014 27/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nNordika coloraci\u00f3n 7.0 con oxidante x 2\nOrkidea ampolla reparadora 20 u\ndos Fiorela m\u00e1scara nutrici\u00f3n 250\nBelkor sh color 5L \u2014 3\n3 Kapris acond keratina 900\nTrevia masc nutrici\u00f3n 250 \u2014 7   \u2190 TACHAR\nDuomo oxidante 30 vol 90 (17)\nNordika ba\u00f1o de crema karit\u00e9 1k x 2\nOrkidea shampoo keratina 5 L x 1\nNordika spray brillo 250 x 3\nBelkor protector t\u00e9rmico x250 \u2014 1"
  },
  {
    "id": "L29",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nPeluquer\u00eda Los \u00c1lamos \u2014 1/11\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n2 ba\u00f1o de crema karit\u00e9 Trevia 1 kg\nFiorela coloraci\u00f3n 8.1 x 4\n5 Nordika protector t\u00e9rmico\n2 Kapris \u00f3leo argan 60\nmedia docena Nordika ox 30 vol x90\nFiorela ox 30 vol 900 (2)\n2 bowl para tintura Montello   \u2190 TACHAR\n1 Fiorela sh nutrici\u00f3n 5 L\nLumine ampolla reparadora \u2014 4 cajas\n1 Belkor ba\u00f1o de crema coco\n17 sachets decolorante Fiorela"
  },
  {
    "id": "L30",
    "texto": "Copi\u00e1 esta lista en un papel, con el encabezado arriba.\n\nEst\u00e9tica Las Moras \u2014 26/12\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nSilkara acond keratina 900 (2)\n6 ox 40 vol Duomo x900\n4 Belkor sh nutrici\u00f3n 300   \u2190 TACHAR\nArgania ox 20 vol x900 \u2014 1\nBrisa acond color 900 x 2\nSilkara sh argan x900 \u2014 2\n1 Belkor ba\u00f1o de crema coco\nBelkor tintura 6.0 x2\nOrkidea \u00f3leo argan x60 \u2014 cuatro\nKapris sh keratina 900 x 2\n2 cajas ampollas Nordika"
  }
];

function crearForm() {
  const form = FormApp.create(TEXTOS.titulo);
  form.setDescription(TEXTOS.inicio);
  form.setCollectEmail(false);
  form.setProgressBar(true);

  form.addCheckboxItem()
    .setTitle(TEXTOS.consentimiento_titulo)
    .setChoiceValues([TEXTOS.consentimiento])
    .setRequired(true);
  const elegir = form.addListItem()
    .setTitle(TEXTOS.elegir_lista)
    .setRequired(true);

  const secciones = LISTAS.map(function (lista) {
    return form.addPageBreakItem()
      .setTitle(TEXTOS.lista + lista.id)
      .setHelpText(lista.texto);
  });

  const final = form.addPageBreakItem()
    .setTitle(TEXTOS.final_titulo)
    .setHelpText(TEXTOS.final_ayuda);
  secciones.forEach(function (seccion) {
    seccion.setGoToPage(final);
  });
  elegir.setChoices(LISTAS.map(function (lista, i) {
    return elegir.createChoice(lista.id, secciones[i]);
  }));

  form.addMultipleChoiceItem()
    .setTitle(TEXTOS.copio_todo)
    .setChoiceValues(TEXTOS.si_no)
    .setRequired(true);
  form.addParagraphTextItem()
    .setTitle(TEXTOS.cambios);
  form.addMultipleChoiceItem()
    .setTitle(TEXTOS.luz)
    .setChoiceValues(TEXTOS.luces)
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle(TEXTOS.papel)
    .setChoiceValues(TEXTOS.papeles)
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle(TEXTOS.angulo)
    .setChoiceValues(TEXTOS.angulos)
    .setRequired(true);

  Logger.log('Para editar (agrega la pregunta de la foto): ' + form.getEditUrl());
  Logger.log('Para mandar: ' + form.getPublishedUrl());
}

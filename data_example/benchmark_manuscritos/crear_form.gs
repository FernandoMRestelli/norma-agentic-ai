// Generado con `python -m internal.benchmark_manuscritos form` (semilla 116).
// No se edita a mano: se cambia el pool o la semilla y se vuelve a generar.
//
// 1. Pegalo en un proyecto nuevo de https://script.google.com y corre crearForm() con la cuenta que va a recibir
//    las fotos (pide permiso para crear Forms).
// 2. Abri el link de edicion que queda en el log y, en la seccion final, agrega la pregunta "Subir archivo":
//    "Foto del pedido", solo imagenes, un archivo, obligatoria. Apps Script no puede crear esa pregunta.
// 3. Manda el link publico: cada persona elige el pedido que quiera en el desplegable.

const TEXTOS = {
  "titulo": "Pedidos escritos a mano",
  "inicio": "\u00a1Hola! Te pido una mano para un trabajo de la facu: estoy midiendo qu\u00e9 tan bien una IA lee pedidos escritos a mano.\n\n1. Eleg\u00ed abajo el pedido que quieras y toc\u00e1 \u00abSiguiente\u00bb: vas a ver los renglones que ten\u00e9s que copiar.\n2. Copialo a mano en un papel, con tu letra de siempre, como si se lo hicieras a tu proveedor.\n3. Arriba, como encabezado, pon\u00e9 \u00abPedido para Norma\u00bb.\n4. Si te equivoc\u00e1s al escribir, tachalo con una l\u00ednea y segu\u00ed, como har\u00edas en un pedido de verdad. No hace falta empezar de nuevo.\n5. Sacale una foto con el celu como la mandar\u00edas por WhatsApp: sin escanear, sin recortar, con la luz que tengas, y subila al final.\n\nNo pongas tu nombre ni datos tuyos en la hoja. La foto se usa solo para este trabajo, no se publica, y se manda a la API del modelo que la lee (OpenAI o Google) para medir. Si prefer\u00eds que no, no pasa nada.",
  "consentimiento_titulo": "Consentimiento",
  "consentimiento": "Acepto que la foto se use para este trabajo y se mande a la API del modelo que la lee",
  "elegir_lista": "Eleg\u00ed un pedido",
  "final_titulo": "La foto y unas preguntas",
  "final_ayuda": "Sub\u00ed la foto de la hoja y contanos c\u00f3mo la sacaste.",
  "copio_todo": "\u00bfEscribiste todos los renglones tal cual?",
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
  "sexo": "Sexo",
  "sexos": [
    "Masculino",
    "Femenino",
    "Prefiero no responder"
  ]
};
const LISTAS = [
  {
    "id": "L01",
    "nombre": "Pedido 01",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nuna masc keratina Belkor 1 kg\nSilkara sh argan x900 \u2014 2\nmedia docena Nordika ox 30 vol x90\nArgania ox 20 vol x900 \u2014 1\nBelkor sh color 5L \u2014 3\nTrevia ampolla reparadora x15 \u2014 2 cajas\nArgania tintura 4.0 \u2014 3\n2 Belkor crema de peinar rulos\nKapris coloraci\u00f3n 6.1 con oxidante \u2014 1\n3 cajas ampolla reparadora Kapris"
  },
  {
    "id": "L02",
    "nombre": "Pedido 02",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nuna docena spray brillo Silkara\nFiorela ox 30 vol 900 (2)\nDuomo coloraci\u00f3n 9.1 (1)\n11 Nordika oxidante 20 vol 90\nLumine ampolla reparadora \u2014 4 cajas\nSilkara masc nutrici\u00f3n 1k \u2014 2\nKapris sh keratina 900 x 2\n1 Kapris sh color 300\nOrkidea shampoo keratina 5 L x 1\nLumine masc keratina x1k \u2014 2"
  },
  {
    "id": "L03",
    "nombre": "Pedido 03",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n3 Lumine acond keratina 300\nBelkor ampolla reparadora 12 u\nKapris ox 10 vol x90 \u2014 doce\nSilkara acond keratina 900 (2)\n2 acond keratina Nordika 900\nNordika masc argan 250 (6)\nmedia docena acond argan Orkidea 300\nFiorela coloraci\u00f3n 8.1 x 4\nOrkidea ampolla reparadora 20 u\nBelkor decol sachet \u2014 10"
  },
  {
    "id": "L04",
    "nombre": "Pedido 04",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nTrevia shampoo matizador 900 (3)\nOrkidea \u00f3leo argan x60 \u2014 cuatro\n2 Fiorela serum puntas\n2 cajas Silkara ampolla reparadora\n1 ba\u00f1o de crema coco Trevia 1k\ndiez gel fijador Kapris 500\n2 Nordika tintura 5.0\n2 Orkidea sh color 900\n1 Silkara sh keratina 900\n10 serum puntas Orkidea"
  },
  {
    "id": "L05",
    "nombre": "Pedido 05",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\ndoce oxidante Belkor 20 vol x90\nTrevia acond nutrici\u00f3n 900 x 5\nNordika spray brillo 250 x 3\nDuomo oxidante 30 vol 90 (17)\nMontello peine de corte (10)\n2 cajas ampollas Nordika\n17 sachets decolorante Fiorela\n3 Kapris acond color 300\nNordika coloraci\u00f3n 7.0 con oxidante x 2\n3 Belkor coloraci\u00f3n 10.1"
  },
  {
    "id": "L06",
    "nombre": "Pedido 06",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nArgania oxidante 10 vol x 90 \u2014 7\nFiorela gel fijador x500 \u2014 3\nBelkor protector t\u00e9rmico x250 \u2014 1\nOrkidea m\u00e1scara keratina x250 (3)\n24 sachets decolorante Duomo\ndos Fiorela m\u00e1scara nutrici\u00f3n 250\nBelkor tintura 6.0 x2\nNordika oxidante 20 vol 900 x 4\nLumine acond argan 300 x 3\n12 u Fiorela ampolla reparadora"
  },
  {
    "id": "L07",
    "nombre": "Pedido 07",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n4 Belkor sh matizador 900\n4 Brisa shampoo nutrici\u00f3n 5 L\ntres serum puntas Lumine\n4 protector t\u00e9rmico Lumine\nOrkidea spray brillo x250 \u2014 3\nMontello papel aluminio 5 kg x 1\n2 cajas ampollas Nordika\nBrisa acond color 900 x 2\n3 Belkor coloraci\u00f3n 10.1\nOrkidea acond color 900 \u2014 2"
  },
  {
    "id": "L08",
    "nombre": "Pedido 08",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n5 Lumine sh nutrici\u00f3n 900\nTrevia serum puntas x60 (11)\nKapris crema de peinar rulos (1)\nKapris sh matizador 300 x 2\n7 Fiorela acond color 900\nLumine crema de peinar rulos x300 \u2014 4\ncuatro Silkara sh matizador 300\n2 Nordika tintura 5.0\n12 u Fiorela ampolla reparadora\nBelkor oxidante 40 vol 900 x 3"
  },
  {
    "id": "L09",
    "nombre": "Pedido 09",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n2 bowl para tintura Montello\nTrevia masc nutrici\u00f3n 250 \u2014 7\nLumine sh nutrici\u00f3n 300 \u2014 6\n8 Orkidea gel fijador\n1 Belkor ba\u00f1o de crema coco\nBelkor tintura 6.0 x2\n1 Brisa sh matizador 5 L\ndos Fiorela m\u00e1scara nutrici\u00f3n 250\n2 ba\u00f1o de crema karit\u00e9 Trevia 1 kg\nTrevia ampolla reparadora x15 \u2014 2 cajas"
  },
  {
    "id": "L10",
    "nombre": "Pedido 10",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\ncuatro Silkara sh matizador 300\n2 cajas Silkara ampolla reparadora\n3 Kapris acond keratina 900\n6 ox 40 vol Duomo x900\n1 capa de corte Montello\n1 Fiorela sh nutrici\u00f3n 5 L\nNordika ba\u00f1o de crema karit\u00e9 1k x 2\nFiorela sh matizador 5 L (1)\nFiorela coloraci\u00f3n 8.1 x 4\nBrisa sh nutrici\u00f3n 900 (5)"
  },
  {
    "id": "L11",
    "nombre": "Pedido 11",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n4 Belkor sh nutrici\u00f3n 300\nArgania polvo decolorante 700 g (2)\n3 cajas ampolla reparadora Kapris\n2 Kapris \u00f3leo argan 60\n6 Silkara crema de peinar rulos\ndoce oxidante Belkor 20 vol x90\nKapris coloraci\u00f3n 6.1 con oxidante \u2014 1\n1 decol Belkor 700\n5 Kapris masc argan 1 kg\n2 Trevia sh argan 5L"
  },
  {
    "id": "L12",
    "nombre": "Pedido 12",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nOrkidea spray brillo x250 \u2014 3\n5 Nordika protector t\u00e9rmico\nArgania polvo decolorante 700 g (2)\n1 Silkara sh keratina 900\n3 Montello pincel para tintura\nDuomo coloraci\u00f3n 9.1 (1)\nuna masc keratina Belkor 1 kg\nLumine ampolla reparadora \u2014 4 cajas\nmedia docena Nordika ox 30 vol x90\nSilkara masc nutrici\u00f3n 1k \u2014 2"
  },
  {
    "id": "L13",
    "nombre": "Pedido 13",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nmedia docena acond argan Orkidea 300\n1 ba\u00f1o de crema coco Trevia 1k\nFiorela ox 30 vol 900 (2)\nOrkidea ampolla reparadora 20 u\nLumine crema de peinar rulos x300 \u2014 4\nArgania tintura 4.0 \u2014 3\nLumine sh nutrici\u00f3n 300 \u2014 6\nKapris sh matizador 300 x 2\n4 Belkor sh nutrici\u00f3n 300\ndiez gel fijador Kapris 500"
  },
  {
    "id": "L14",
    "nombre": "Pedido 14",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n3 Kapris acond keratina 900\nMontello peine de corte (10)\nOrkidea shampoo keratina 5 L x 1\nArgania oxidante 10 vol x 90 \u2014 7\n2 Belkor crema de peinar rulos\nOrkidea m\u00e1scara keratina x250 (3)\ntres serum puntas Lumine\nBelkor ampolla reparadora 12 u\nNordika coloraci\u00f3n 7.0 con oxidante x 2\nOrkidea \u00f3leo argan x60 \u2014 cuatro"
  },
  {
    "id": "L15",
    "nombre": "Pedido 15",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nKapris crema de peinar rulos (1)\nTrevia masc nutrici\u00f3n 250 \u2014 7\nBelkor protector t\u00e9rmico x250 \u2014 1\nKapris sh keratina 900 x 2\n2 cajas Silkara ampolla reparadora\nKapris ox 10 vol x90 \u2014 doce\n1 Kapris sh color 300\nBelkor decol sachet \u2014 10\n8 Orkidea gel fijador\nDuomo coloraci\u00f3n 9.1 (1)"
  },
  {
    "id": "L16",
    "nombre": "Pedido 16",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n3 Lumine acond keratina 300\n2 bowl para tintura Montello\nFiorela gel fijador x500 \u2014 3\nBelkor tintura 6.0 x2\nArgania ox 20 vol x900 \u2014 1\nFiorela sh matizador 5 L (1)\nBelkor oxidante 40 vol 900 x 3\nLumine ampolla reparadora \u2014 4 cajas\nuna docena spray brillo Silkara\n2 Orkidea sh color 900"
  },
  {
    "id": "L17",
    "nombre": "Pedido 17",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nNordika spray brillo 250 x 3\nTrevia shampoo matizador 900 (3)\n10 serum puntas Orkidea\nLumine masc keratina x1k \u2014 2\nOrkidea ampolla reparadora 20 u\n3 Belkor coloraci\u00f3n 10.1\nOrkidea \u00f3leo argan x60 \u2014 cuatro\nSilkara acond keratina 900 (2)\n17 sachets decolorante Fiorela\nSilkara sh argan x900 \u2014 2"
  },
  {
    "id": "L18",
    "nombre": "Pedido 18",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n5 Nordika protector t\u00e9rmico\nuna docena spray brillo Silkara\nNordika coloraci\u00f3n 7.0 con oxidante x 2\nNordika masc argan 250 (6)\n1 decol Belkor 700\nTrevia ampolla reparadora x15 \u2014 2 cajas\n4 protector t\u00e9rmico Lumine\n11 Nordika oxidante 20 vol 90\nTrevia acond nutrici\u00f3n 900 x 5\n4 Brisa shampoo nutrici\u00f3n 5 L"
  },
  {
    "id": "L19",
    "nombre": "Pedido 19",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n3 Kapris acond color 300\n1 capa de corte Montello\nBelkor sh color 5L \u2014 3\n2 Trevia sh argan 5L\ndos Fiorela m\u00e1scara nutrici\u00f3n 250\nBelkor ampolla reparadora 12 u\nFiorela coloraci\u00f3n 8.1 x 4\nTrevia serum puntas x60 (11)\nOrkidea acond color 900 \u2014 2\nLumine acond argan 300 x 3"
  },
  {
    "id": "L20",
    "nombre": "Pedido 20",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n4 Belkor sh matizador 900\n5 Kapris masc argan 1 kg\n7 Fiorela acond color 900\n2 Nordika tintura 5.0\n6 ox 40 vol Duomo x900\nuna masc keratina Belkor 1 kg\n5 Lumine sh nutrici\u00f3n 900\nMontello papel aluminio 5 kg x 1\nDuomo oxidante 30 vol 90 (17)\n3 cajas ampolla reparadora Kapris"
  },
  {
    "id": "L21",
    "nombre": "Pedido 21",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nBrisa sh nutrici\u00f3n 900 (5)\nKapris coloraci\u00f3n 6.1 con oxidante \u2014 1\nOrkidea m\u00e1scara keratina x250 (3)\n2 cajas ampollas Nordika\n24 sachets decolorante Duomo\n1 Belkor ba\u00f1o de crema coco\nKapris ox 10 vol x90 \u2014 doce\n1 Brisa sh matizador 5 L\nNordika ba\u00f1o de crema karit\u00e9 1k x 2\n2 acond keratina Nordika 900"
  },
  {
    "id": "L22",
    "nombre": "Pedido 22",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nArgania tintura 4.0 \u2014 3\nFiorela gel fijador x500 \u2014 3\n3 Montello pincel para tintura\n1 Fiorela sh nutrici\u00f3n 5 L\n2 Fiorela serum puntas\nmedia docena acond argan Orkidea 300\nBrisa acond color 900 x 2\n2 ba\u00f1o de crema karit\u00e9 Trevia 1 kg\n12 u Fiorela ampolla reparadora\n2 Kapris \u00f3leo argan 60"
  },
  {
    "id": "L23",
    "nombre": "Pedido 23",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n17 sachets decolorante Fiorela\n1 decol Belkor 700\nNordika oxidante 20 vol 900 x 4\nBelkor tintura 6.0 x2\n5 Nordika protector t\u00e9rmico\n11 Nordika oxidante 20 vol 90\n2 Orkidea sh color 900\nmedia docena Nordika ox 30 vol x90\nTrevia ampolla reparadora x15 \u2014 2 cajas\n6 Silkara crema de peinar rulos"
  },
  {
    "id": "L24",
    "nombre": "Pedido 24",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nOrkidea spray brillo x250 \u2014 3\nSilkara sh argan x900 \u2014 2\nBelkor sh color 5L \u2014 3\ndoce oxidante Belkor 20 vol x90\n4 Brisa shampoo nutrici\u00f3n 5 L\nKapris coloraci\u00f3n 6.1 con oxidante \u2014 1\nNordika ba\u00f1o de crema karit\u00e9 1k x 2\ndiez gel fijador Kapris 500\nFiorela sh matizador 5 L (1)\nBelkor ampolla reparadora 12 u"
  },
  {
    "id": "L25",
    "nombre": "Pedido 25",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nLumine crema de peinar rulos x300 \u2014 4\n3 Kapris acond keratina 900\ncuatro Silkara sh matizador 300\nMontello peine de corte (10)\nOrkidea ampolla reparadora 20 u\nFiorela coloraci\u00f3n 8.1 x 4\nTrevia masc nutrici\u00f3n 250 \u2014 7\n8 Orkidea gel fijador\nNordika spray brillo 250 x 3\nBelkor decol sachet \u2014 10"
  },
  {
    "id": "L26",
    "nombre": "Pedido 26",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n3 Kapris acond color 300\nTrevia acond nutrici\u00f3n 900 x 5\n2 Kapris \u00f3leo argan 60\nTrevia serum puntas x60 (11)\nLumine sh nutrici\u00f3n 300 \u2014 6\n2 cajas Silkara ampolla reparadora\ntres serum puntas Lumine\n2 Belkor crema de peinar rulos\nOrkidea acond color 900 \u2014 2\n3 Belkor coloraci\u00f3n 10.1"
  },
  {
    "id": "L27",
    "nombre": "Pedido 27",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\nKapris sh keratina 900 x 2\nDuomo coloraci\u00f3n 9.1 (1)\nKapris sh matizador 300 x 2\nNordika oxidante 20 vol 900 x 4\n6 Silkara crema de peinar rulos\nBelkor protector t\u00e9rmico x250 \u2014 1\n3 cajas ampolla reparadora Kapris\nmedia docena acond argan Orkidea 300\nBrisa acond color 900 x 2\n6 ox 40 vol Duomo x900"
  },
  {
    "id": "L28",
    "nombre": "Pedido 28",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n1 Brisa sh matizador 5 L\n2 Trevia sh argan 5L\nSilkara acond keratina 900 (2)\n1 Kapris sh color 300\nArgania tintura 4.0 \u2014 3\nBrisa sh nutrici\u00f3n 900 (5)\ndiez gel fijador Kapris 500\nLumine masc keratina x1k \u2014 2\nLumine ampolla reparadora \u2014 4 cajas\nArgania ox 20 vol x900 \u2014 1"
  },
  {
    "id": "L29",
    "nombre": "Pedido 29",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n1 Fiorela sh nutrici\u00f3n 5 L\nTrevia shampoo matizador 900 (3)\n12 u Fiorela ampolla reparadora\nSilkara masc nutrici\u00f3n 1k \u2014 2\nArgania oxidante 10 vol x 90 \u2014 7\n2 Nordika tintura 5.0\nLumine acond argan 300 x 3\nDuomo oxidante 30 vol 90 (17)\ndoce oxidante Belkor 20 vol x90\n1 Silkara sh keratina 900"
  },
  {
    "id": "L30",
    "nombre": "Pedido 30",
    "texto": "Escrib\u00ed este pedido en un papel e inclu\u00ed el encabezado.\n\nPedido para Norma\n\n2 sh keratina Nordika x900\nKapris oxidante 20 vol x900 \u2014 (1)\nKapris tintura 7.1 x3\nmedia docena masc argan Lumine 250\nSilkara protector t\u00e9rmico (7)\n2 ba\u00f1o de crema karit\u00e9 Trevia 1 kg\n2 cajas ampollas Nordika\n1 ba\u00f1o de crema coco Trevia 1k\nNordika coloraci\u00f3n 7.0 con oxidante x 2\nArgania ox 20 vol x900 \u2014 1\nuna masc keratina Belkor 1 kg\n1 Belkor ba\u00f1o de crema coco\n2 acond keratina Nordika 900\nMontello papel aluminio 5 kg x 1\n24 sachets decolorante Duomo"
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
      .setTitle(lista.nombre)
      .setHelpText(lista.texto);
  });

  const final = form.addPageBreakItem()
    .setTitle(TEXTOS.final_titulo)
    .setHelpText(TEXTOS.final_ayuda);
  secciones.forEach(function (seccion) {
    seccion.setGoToPage(final);
  });
  elegir.setChoices(LISTAS.map(function (lista, i) {
    return elegir.createChoice(lista.nombre, secciones[i]);
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
    .setTitle(TEXTOS.sexo)
    .setChoiceValues(TEXTOS.sexos)
    .setRequired(true);

  Logger.log('Para editar (agrega la pregunta de la foto): ' + form.getEditUrl());
  Logger.log('Para mandar: ' + form.getPublishedUrl());
}

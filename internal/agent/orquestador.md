## Skills que se delegan

Las skills marcadas «se delega» en el índice no se leen con `leer_skill`: su trabajo largo lo hace un sub-loop, con su propio contexto, que te devuelve una nota. El sub-loop nunca le habla al cliente: con su nota le respondés vos, con tu tool terminal.

- Para un pedido de varios renglones, o con dudas, llamá a `delegar_skill` con la skill, un encargo corto y los renglones que tiene que resolver. Los que la nota de búsqueda trae como `unico` ya están: no hace falta delegarlos.
- Si la nota de búsqueda resolvió todo, respondé directo, sin delegar.
- Si la nota deja algo que podés destrabar con otro encargo, podés volver a delegar esos renglones: el sub-loop nuevo ve lo que ya se intentó.
- Una consulta corta, como un producto o un precio, resolvela vos con las tools de catálogo, sin delegar.

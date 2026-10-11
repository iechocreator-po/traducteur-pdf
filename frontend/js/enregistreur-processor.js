// Processeur AudioWorklet pour la capture micro (feature bilbao — remplace le
// ScriptProcessorNode déprécié utilisé jusqu'ici, voir module-laboratoire.js).
// Tourne sur le thread audio dédié, jamais sur le thread principal — c'est
// tout l'intérêt par rapport à ScriptProcessorNode.
//
// Rôle strictement identique à l'ancien code : accumuler le PCM mono en
// morceaux d'environ 4096 échantillons (même cadence que l'ancien
// `createScriptProcessor(4096, 1, 1)`) et les transférer au thread principal
// via `port.postMessage`, sans jamais écrire dans `outputs` — la sortie reste
// silencieuse par défaut, donc connecter ce nœud à `destination` (nécessaire
// pour qu'il soit sollicité par le graphe audio) ne produit aucun écho du
// micro dans les haut-parleurs.

const TAILLE_CIBLE = 4096;

class EnregistreurProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._tampon = [];
    this._tailleAccumulee = 0;
  }

  process(inputs) {
    const canal = inputs[0] && inputs[0][0];
    if (canal && canal.length) {
      // Le buffer d'entrée est réutilisé par le moteur audio au prochain
      // appel : on doit en copier le contenu avant de l'accumuler.
      this._tampon.push(canal.slice());
      this._tailleAccumulee += canal.length;

      if (this._tailleAccumulee >= TAILLE_CIBLE) {
        const fusion = new Float32Array(this._tailleAccumulee);
        let decalage = 0;
        for (const morceau of this._tampon) {
          fusion.set(morceau, decalage);
          decalage += morceau.length;
        }
        this._tampon = [];
        this._tailleAccumulee = 0;
        // Transfert (pas de copie) : le buffer change de propriétaire.
        this.port.postMessage(fusion, [fusion.buffer]);
      }
    }
    return true; // false arrêterait définitivement le processeur
  }
}

registerProcessor("enregistreur-pcm", EnregistreurProcessor);

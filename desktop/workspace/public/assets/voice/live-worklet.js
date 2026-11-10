// Captures the microphone as 24 kHz mono PCM16 for live voice chat and reports a 0 to 1 level.
// Runs on the audio thread. Nothing is stored; each block is posted to the page and dropped.
const RATE = 24000;
const BLOCK = 960; // 40 ms

class Pcm16Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / RATE;
    this.pos = 0;
    this.out = new Int16Array(BLOCK);
    this.filled = 0;
    this.energy = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input || !input.length) return true;
    let pos = this.pos;
    while (pos < input.length) {
      const i = Math.floor(pos);
      const a = input[i];
      const b = i + 1 < input.length ? input[i + 1] : a;
      const value = Math.max(-1, Math.min(1, a + (b - a) * (pos - i)));
      this.out[this.filled++] = value < 0 ? value * 32768 : value * 32767;
      this.energy += value * value;
      if (this.filled === BLOCK) {
        const level = Math.min(1, Math.sqrt(this.energy / BLOCK) * 3);
        this.port.postMessage({pcm: this.out.buffer, level}, [this.out.buffer]);
        this.out = new Int16Array(BLOCK);
        this.filled = 0;
        this.energy = 0;
      }
      pos += this.step;
    }
    this.pos = pos - input.length;
    return true;
  }
}

registerProcessor('pcm16-capture', Pcm16Capture);

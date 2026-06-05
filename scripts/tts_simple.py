"""最小化 TTS CLI — 只输出静音 WAV，排除所有外部依赖"""
import sys, struct, os

def main():
    if len(sys.argv) < 3:
        print("usage: tts_simple.py --output PATH", file=sys.stderr)
        sys.exit(1)
    output = sys.argv[-1]
    d = os.path.dirname(output)
    if d: os.makedirs(d, exist_ok=True)
    # 生成 1 秒 16kHz 16bit mono 静音 WAV
    rate, bits, ch = 16000, 16, 1
    data_len = rate * bits // 8 * ch
    with open(output, 'wb') as f:
        f.write(b'RIFF')
        f.write(struct.pack('<I', 36 + data_len))
        f.write(b'WAVEfmt ')
        f.write(struct.pack('<IHHIIHH', 16, 1, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits))
        f.write(b'data')
        f.write(struct.pack('<I', data_len))
        f.write(b'\x00' * data_len)
    print(output, flush=True)

if __name__ == '__main__':
    main()

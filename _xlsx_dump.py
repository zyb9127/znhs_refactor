import sys, zipfile, re
import xml.etree.ElementTree as ET

F = 'docs/天津/天津AI营销话术.xlsx'
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
z = zipfile.ZipFile(F)

# shared strings
shared = []
root = ET.fromstring(z.read('xl/sharedStrings.xml'))
for si in root.findall(f'{NS}si'):
    # 拼接所有 t（富文本会拆成多段）
    parts = [t.text or '' for t in si.iter(f'{NS}t')]
    shared.append(''.join(parts))

def col_idx(ref):
    m = re.match(r'([A-Z]+)(\d+)', ref)
    letters = m.group(1)
    n = 0
    for c in letters:
        n = n * 26 + (ord(c) - 64)
    return n - 1, int(m.group(2))

def dump(sheet_path, sheet_name, max_rows=None):
    print(f'\n================= {sheet_name} ({sheet_path}) =================')
    root = ET.fromstring(z.read(sheet_path))
    rows = {}
    maxc = 0
    for row in root.iter(f'{NS}row'):
        for c in row.findall(f'{NS}c'):
            ref = c.get('r'); t = c.get('t')
            ci, ri = col_idx(ref)
            maxc = max(maxc, ci)
            v = c.find(f'{NS}v')
            isv = c.find(f'{NS}is')
            if t == 's' and v is not None:
                val = shared[int(v.text)]
            elif isv is not None:
                val = ''.join(x.text or '' for x in isv.iter(f'{NS}t'))
            elif v is not None:
                val = v.text
            else:
                val = ''
            rows.setdefault(ri, {})[ci] = val
    for ri in sorted(rows):
        if max_rows and ri > max_rows:
            break
        cells = rows[ri]
        line = []
        for ci in range(maxc + 1):
            val = (cells.get(ci) or '').replace('\n', '\\n')
            line.append(f'[{chr(65+ci) if ci<26 else ci}]{val}')
        print(f'R{ri}: ' + ' | '.join(line))

target = sys.argv[1] if len(sys.argv) > 1 else 'both'
if target in ('1', 'both'):
    dump('xl/worksheets/sheet1.xml', '天津提交版')
if target in ('2', 'both'):
    dump('xl/worksheets/sheet2.xml', '活动以及话术模板')

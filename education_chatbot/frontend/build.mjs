import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';

const root = new URL('./', import.meta.url);
const vendor = new URL('public/vendor/', root);
await mkdir(vendor, { recursive: true });
for (const [source, target] of [
    ['marked/lib/marked.umd.js', 'marked.js'],
    ['dompurify/dist/purify.min.js', 'purify.js'],
    ['katex/dist/katex.min.js', 'katex.js'],
    ['katex/dist/katex.min.css', 'katex.css'],
    ['katex/dist/contrib/auto-render.min.js', 'auto-render.js'],
    ['katex/dist/fonts/', 'fonts/'],
    ['@fontsource-variable/manrope/files/manrope-latin-wght-normal.woff2', 'manrope.woff2'],
]) {
    await cp(new URL('node_modules/' + source, root), new URL(target, vendor), { recursive: true });
}

const licenses = new URL('licenses/', vendor);
await mkdir(licenses, { recursive: true });
for (const [source, name] of [
    ['marked', 'marked'],
    ['dompurify', 'dompurify'],
    ['katex', 'katex'],
    ['lucide-static', 'lucide'],
    ['@fontsource-variable/manrope', 'manrope'],
]) {
    await cp(new URL('node_modules/' + source + '/LICENSE', root), new URL(name + '.txt', licenses));
}

const names = [
    'arrow-right', 'arrow-up', 'book-open', 'check', 'chevron-right', 'circle-help', 'clipboard',
    'download', 'graduation-cap', 'history', 'list-checks', 'menu', 'message-circle', 'plus',
    'scan-text', 'search', 'settings-2', 'shield-check', 'sparkles', 'trash-2', 'x',
];
const symbols = await Promise.all(names.map(async (name) => {
    const file = await readFile(new URL('node_modules/lucide-static/icons/' + name + '.svg', root), 'utf8');
    const svgStart = file.indexOf('<svg');
    const artwork = file.slice(file.indexOf('>', svgStart) + 1, file.lastIndexOf('</svg>'));
    return '<symbol id="' + name + '" viewBox="0 0 24 24">' + artwork + '</symbol>';
}));
await writeFile(
    new URL('icons.svg', vendor),
    '<svg xmlns="http://www.w3.org/2000/svg">' + symbols.join('') + '</svg>\n',
);

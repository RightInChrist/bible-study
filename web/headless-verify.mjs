// Minimal JSDOM-style verification: fetch the prod build bundle, load it
// into a JSDOM, wait for the fetch-driven UI to mount, and dump the
// rendered article cards. Acts as the closest-thing-to-a-browser check
// available without a real Chromium.
import { JSDOM, ResourceLoader } from 'jsdom';
import { readFileSync, readdirSync } from 'node:fs';

const distDir = './dist/assets';
const files = readdirSync(distDir);
const jsFile = files.find((f) => f.endsWith('.js'));
const cssFile = files.find((f) => f.endsWith('.css'));
const bundleSrc = readFileSync(`${distDir}/${jsFile}`, 'utf8');
const cssSrc = readFileSync(`${distDir}/${cssFile}`, 'utf8');

class StubLoader extends ResourceLoader {
  fetch(url, options) {
    if (url.includes('/api/v1/admin/fixture-status')) {
      return Promise.resolve(Buffer.from(JSON.stringify({
        disk_manifest_hash: 'aabbccddeeff112233445566',
        db_fixture_version: 'aabbccddeeff112233445566',
        stale: false,
        last_imported_at: '2026-05-03T20:32:00Z',
      })));
    }
    if (url.includes('/api/v1/sentences') && url.includes('chapter=5')) {
      return Promise.resolve(Buffer.from(JSON.stringify({
        chapter: 5,
        sentences: [
          {
            sentence_id: 'mat-5-1', chapter: 5, ordinal_in_chapter: 1,
            start_verse: 1, end_verse: 1,
            starts_at_verse_boundary: true, ends_at_verse_boundary: false,
            text_preview: 'Ἰδὼν δὲ τοὺς ὄχλους...', word_count: 8, is_red_letter: false,
          },
          {
            sentence_id: 'mat-5-4', chapter: 5, ordinal_in_chapter: 4,
            start_verse: 3, end_verse: 3,
            starts_at_verse_boundary: true, ends_at_verse_boundary: true,
            text_preview: 'Μακάριοι οἱ πτωχοὶ τῷ πνεύματι...', word_count: 11, is_red_letter: true,
          },
        ],
      })));
    }
    if (url.includes('/parallel')) {
      const isRed = url.includes('mat-5-4');
      return Promise.resolve(Buffer.from(JSON.stringify({
        sentence_id: isRed ? 'mat-5-4' : 'mat-5-1',
        chapter: 5, ordinal_in_chapter: isRed ? 4 : 1,
        start_chapter: 5, start_verse: isRed ? 3 : 1,
        end_chapter: 5, end_verse: isRed ? 3 : 1,
        starts_at_verse_boundary: true, ends_at_verse_boundary: isRed ? true : false,
        word_count: 8,
        text_sblgnt: isRed ? 'Μακάριοι οἱ πτωχοὶ' : 'Ἰδὼν δὲ τοὺς ὄχλους',
        byzantine: [{ chapter: 5, verse: isRed ? 3 : 1, text: 'Greek text...' }],
        english: [
          { translation: 'BSB', verses: [{ chapter: 5, verse: isRed ? 3 : 1, text: 'BSB text' }] },
          { translation: 'BLB', verses: [{ chapter: 5, verse: isRed ? 3 : 1, text: 'BLB text' }] },
          { translation: 'WEB', verses: [{ chapter: 5, verse: isRed ? 3 : 1, text: 'WEB text' }] },
        ],
        bib_interlinear: [],
        is_red_letter: isRed,
      })));
    }
    return super.fetch(url, options);
  }
}

const dom = new JSDOM(`<!doctype html><html><head><style>${cssSrc}</style></head><body><div id="root"></div></body></html>`, {
  url: 'http://localhost:5174/chapter/5',
  runScripts: 'outside-only',
  resources: new StubLoader(),
  pretendToBeVisual: true,
});
const { window } = dom;
window.fetch = async (urlOrReq) => {
  const url = typeof urlOrReq === 'string' ? urlOrReq : urlOrReq.url;
  const loader = new StubLoader();
  const buf = await loader.fetch(url);
  return new window.Response(buf.toString(), { status: 200, headers: { 'content-type': 'application/json' } });
};
window.eval(bundleSrc);

await new Promise((r) => setTimeout(r, 400));

const articles = window.document.querySelectorAll('[role="article"]');
console.log(`articles rendered: ${articles.length}`);
articles.forEach((a) => {
  const sid = a.getAttribute('data-sentence-id');
  const red = a.getAttribute('data-red-letter');
  const anchor = a.querySelector('[aria-label="starts at verse boundary"]') ? '⚓' : ' ';
  console.log(`  ${sid}  red=${red}  ${anchor}`);
});
const pill = window.document.querySelector('.status-pill');
console.log(`status pill: ${pill?.textContent ?? 'MISSING'}`);
const heading = window.document.querySelector('.chapter-heading');
console.log(`heading: ${heading?.textContent ?? 'MISSING'}`);
const caveat = window.document.querySelectorAll('.projection-caveat');
console.log(`projection caveat (~) glyphs: ${caveat.length}`);

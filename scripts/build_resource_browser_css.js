const fs = require('node:fs');
const postcss = require('postcss');
const prefixSelector = require('postcss-prefix-selector');

const packageCss = fs.readFileSync('node_modules/@cubone/react-file-manager/dist/style.css', 'utf8');
const adapterCss = fs.readFileSync('web_src/trade_resource_browser.css', 'utf8');

postcss([
  prefixSelector({
    prefix: '.trade-resource-browser-main',
    transform(prefix, selector, prefixedSelector) {
      if (selector === 'html' || selector === 'body' || selector === ':root') return prefix;
      return prefixedSelector;
    },
  }),
]).process(packageCss, { from: undefined }).then((result) => {
  fs.writeFileSync(
    'web/vendor/trade-resource-browser.css',
    `${result.css.trimEnd()}\n${adapterCss.trimEnd()}\n`,
    'utf8',
  );
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

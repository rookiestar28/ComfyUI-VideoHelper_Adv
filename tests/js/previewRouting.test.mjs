import test from 'node:test';
import assert from 'node:assert/strict';

import { shouldUseAdvancedPreview } from '../../web/js/previewRouting.js';

test('Input Only keeps input previews on advanced routing', () => {
  assert.equal(
    shouldUseAdvancedPreview({
      advancedPreviews: 'Input Only',
      isInput: true,
      format: 'video/mp4',
    }),
    true,
  );
});

test('Input Only still uses advanced routing for completed output videos', () => {
  assert.equal(
    shouldUseAdvancedPreview({
      advancedPreviews: 'Input Only',
      isInput: false,
      format: 'video/h264-mp4',
    }),
    true,
  );
});

test('Input Only does not force advanced routing for non-video output states', () => {
  assert.equal(
    shouldUseAdvancedPreview({
      advancedPreviews: 'Input Only',
      isInput: false,
      format: 'image/gif',
    }),
    false,
  );
});

test('Never disables advanced routing even for completed outputs', () => {
  assert.equal(
    shouldUseAdvancedPreview({
      advancedPreviews: 'Never',
      isInput: false,
      format: 'video/h264-mp4',
    }),
    false,
  );
});

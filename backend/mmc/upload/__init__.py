"""Upload-mode processing (M5U): manual PDFs, CSV logs, video clips.

Blobs arrive first via POST .../blobs; these helpers turn them into
evidence/observations through the normal append paths. Uploads alone never
move posteriors — CSV-derived tests go through the same evaluator +
test_result folds as the live result route.
"""
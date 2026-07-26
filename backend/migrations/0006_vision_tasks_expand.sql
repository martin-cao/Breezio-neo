-- anomaly-detection results were being computed but silently never persisted
-- (VISION_TASKS didn't include the task, and this constraint would have
-- rejected the row anyway); scene-caption is the new Moondream2 VLM task.
ALTER TABLE vision_events
  DROP CONSTRAINT vision_task_check;
ALTER TABLE vision_events
  ADD CONSTRAINT vision_task_check CHECK (
    task IN ('scene-classification', 'person-detection', 'face-identification', 'anomaly-detection', 'scene-caption')
  );

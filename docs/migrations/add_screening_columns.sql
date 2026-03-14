-- Migration: Add screening columns to interactions table
-- Run this once in the Supabase SQL Editor (or via supabase db push)
-- Safe to run multiple times (uses IF NOT EXISTS / ON CONFLICT DO NOTHING)

-- 1. Add screening results columns to interactions
ALTER TABLE interactions
  ADD COLUMN IF NOT EXISTS screening_scores       JSONB,
  ADD COLUMN IF NOT EXISTS screening_recommendation TEXT;

-- 2. Seed the screening interview prompt into platform_config
-- Customise the value here to change AI-DAN's screening behaviour
INSERT INTO platform_config (key, value, description)
VALUES (
  'screening_interview',
  '"CONVERSATION STYLE:\n- Be professional, warm, and natural — like a senior recruiter on the phone\n- Ask ONE question at a time; wait for the full answer before continuing\n- Keep each spoken turn under 30 seconds (about 60-80 words max)\n- If an answer is unclear, ask ONE brief follow-up for clarity\n- Do not repeat questions already answered\n- Do not read out question numbers or labels — ask naturally\n\nCALL FLOW:\n1. Introduce yourself and confirm the candidate has a few minutes\n2. Work through each screening question in order\n3. After each answer, acknowledge briefly before moving on\n4. When all questions are covered, thank the candidate and explain next steps\n5. Close the call professionally and call the end_call tool\n\nIMPORTANT RULES:\n- Never mention scores, weights, or that you are AI scoring\n- If the candidate wants to end early, thank them and close\n- Keep the tone encouraging and respectful throughout\n- When the call is clearly over, call end_call exactly once"',
  'System prompt body for AI-DAN screening interview calls. Supports {company_name}, {role_title}, {candidate_name} template variables.'
)
ON CONFLICT (key) DO NOTHING;

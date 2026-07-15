-- ============================================================================
-- Migration 004 — clear views (v0.4)
--
-- Local-only readable views that RE-JOIN pseudonymised state with the
-- identity vault, so the CLIENT browsing their own file sees real names.
--
-- Privacy model: these are views — computed at read time, nothing clear is
-- ever stored. They exist only where identity.map has rows: `tca export`
-- copies TABLES only and drops the identity schema, so on the shared copy
-- this recombination is not merely forbidden — the data to do it is absent.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS clear;

CREATE OR REPLACE VIEW clear.users AS
  SELECT u.user_pseudo,
         i.full_name,
         i.email,
         i.name AS username,
         u.site_role,
         u.auth_setting,
         u.last_login_at
  FROM state.v_users_current u
  LEFT JOIN identity.map i ON i.pseudonym = u.user_pseudo;

CREATE OR REPLACE VIEW clear.group_members AS
  SELECT g.name AS group_name,
         m.user_pseudo,
         i.full_name,
         i.email
  FROM state.v_groups_current g
  JOIN state.group_members m ON m.run_id = g.run_id AND m.group_luid = g.group_luid
  LEFT JOIN identity.map i ON i.pseudonym = m.user_pseudo;

CREATE OR REPLACE VIEW clear.content_owners AS
  SELECT c.item_type,
         c.name AS item_name,
         p.name AS project_name,
         c.owner_pseudo,
         i.full_name AS owner_full_name,
         i.email AS owner_email,
         c.updated_at
  FROM state.v_content_current c
  LEFT JOIN state.projects p ON p.run_id = c.run_id AND p.project_luid = c.project_luid
  LEFT JOIN identity.map i ON i.pseudonym = c.owner_pseudo;

CREATE OR REPLACE VIEW clear.permission_rules AS
  SELECT r.target_type,
         r.target_luid,
         r.is_default_template,
         r.template_for,
         r.grantee_type,
         CASE WHEN r.grantee_type = 'user' THEN coalesce(i.email, i.full_name, r.grantee_id)
              ELSE coalesce(g.name, r.grantee_id)
         END AS grantee,
         r.capability,
         r.mode
  FROM state.v_permission_rules_current r
  LEFT JOIN identity.map i
    ON r.grantee_type = 'user' AND i.pseudonym = r.grantee_id
  LEFT JOIN state.v_groups_current g
    ON r.grantee_type = 'group' AND g.group_luid = r.grantee_id;

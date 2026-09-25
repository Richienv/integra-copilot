-- 04_redteam_sandbox.sql
-- The role for the red-team sandbox. Attacks replayed WITHOUT the L3 lock (stacks L1 and L1+L2) run in a
-- throwaway database as this role, never as the postgres superuser.
--
-- redteam_sandbox can log in and owns the throwaway database's data, so an unguarded write really takes
-- effect there (that is the point: it shows what the prompt lock alone does not stop). But it is
-- NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION and NOBYPASSRLS, and it is a member of no other role,
-- so it cannot SET ROLE to one that can read or write server files, run server programs or signal other
-- backends. copilot.sandbox.assert_safe_role checks all of this before every statement.
--
-- This file runs again before every rebuild of the sandbox. It resets every session default of the role
-- first: a replayed statement that commits itself can run ALTER ROLE redteam_sandbox SET ..., and that must
-- not reach the rebuild or the next attack.
--
-- copilot.sandbox creates the throwaway database itself (CREATE DATABASE cannot run inside a transaction
-- block, so it is not in this file) and then loads sql/01, the demo data and sql/02 into it. Run this file
-- as the database owner / superuser.

do $$
declare r record;
begin
  if not exists (select 1 from pg_roles where rolname = 'redteam_sandbox') then
    create role redteam_sandbox login nosuperuser nocreatedb nocreaterole noreplication nobypassrls;
  else
    alter role redteam_sandbox login nosuperuser nocreatedb nocreaterole noreplication nobypassrls;
  end if;
  -- no role memberships at all, whoever granted them
  for r in select g.rolname as role, a.rolname as grantor from pg_auth_members m
           join pg_roles g on g.oid = m.roleid join pg_roles a on a.oid = m.grantor
           where m.member = (select oid from pg_roles where rolname = 'redteam_sandbox') loop
    execute format('revoke %I from redteam_sandbox granted by %I', r.role, r.grantor);
  end loop;
  -- no session defaults left by an earlier attack, in any database
  alter role redteam_sandbox reset all;
  for r in select d.datname from pg_db_role_setting s join pg_database d on d.oid = s.setdatabase
           where s.setrole = (select oid from pg_roles where rolname = 'redteam_sandbox') loop
    execute format('alter role redteam_sandbox in database %I reset all', r.datname);
  end loop;
end $$;

-- Keep every transaction in the sandbox short, as a harness safety net (the production L3 role has its own
-- timeout; the prompt lock alone does not).
alter role redteam_sandbox set statement_timeout = '5s';

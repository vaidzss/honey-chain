"""FPO, processor and KVIC console data.

Three audiences, three different questions, so three shapes rather than one
generic "dashboard" that answers none of them well:

- **FPO** aggregates supply. It wants to know which member beekeepers are
  producing, how much is still declarable this season, and what is owed.
- **Processor** transforms. It wants what is in its custody right now, what is
  blocked on a lab result, and how much of the honey it holds is packable.
- **KVIC** administers a scheme. It wants scheme attribution -- are the boxes we
  distributed actually producing? -- and where the fraud risk is concentrated.

That last one is the deliverable people forget. KVIC has handed out 2.46 lakh
bee boxes; nobody can currently answer "which of them produced honey this
season". `scheme_attribution` answers exactly that, and it is the number a
ministry cares about more than any traceability feature.
"""

from __future__ import annotations

import logging

log = logging.getLogger("aurabee.consoles")


# --------------------------------------------------------------------------
# FPO / collection centre
# --------------------------------------------------------------------------
def fpo_console(conn, actor_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, name, kind, district, state FROM actors WHERE id=%s",
                    (actor_id,))
        me = cur.fetchone()
        if not me:
            raise ValueError(f"unknown actor {actor_id}")

        # Members are the beekeepers in the same cluster. In a real deployment
        # this is an explicit membership table; here the cluster is the proxy.
        cur.execute("""
            -- Every aggregate is a scalar subquery, not a join. Joining hives
            -- to envelopes to events multiplies rows and silently inflates
            -- every count -- which is exactly how this first reported 2,696 kg
            -- declared against a 1,012 kg ceiling.
            SELECT act.id::text AS actor_id, act.name, act.phone,
                   (SELECT count(*) FROM hives h JOIN apiaries a2 ON a2.id = h.apiary_id
                     WHERE a2.actor_id = act.id AND h.status='active') AS hives,
                   (SELECT count(*) FROM hives h JOIN apiaries a2 ON a2.id = h.apiary_id
                     WHERE a2.actor_id = act.id AND h.is_sentinel
                       AND h.status='active')                          AS sentinels,
                   coalesce((SELECT sum(e.p90_kg) FROM yield_envelopes e
                              WHERE e.id IN (SELECT DISTINCT ON (a3.id) e2.id
                                             FROM apiaries a3
                                             JOIN yield_envelopes e2 ON e2.apiary_id = a3.id
                                             WHERE a3.actor_id = act.id
                                             ORDER BY a3.id, e2.created_at DESC)), 0)
                                                                       AS ceiling_kg,
                   -- declared only against those same current envelopes, so the
                   -- two numbers are comparable
                   coalesce((SELECT sum(b.declared_kg) FROM batches b
                              WHERE b.owner_actor_id = act.id
                                AND b.envelope_id IN (
                                    SELECT DISTINCT ON (a4.id) e3.id
                                    FROM apiaries a4
                                    JOIN yield_envelopes e3 ON e3.apiary_id = a4.id
                                    WHERE a4.actor_id = act.id
                                    ORDER BY a4.id, e3.created_at DESC)), 0)
                                                                       AS declared_kg,
                   (SELECT count(*) FROM hive_events ev
                     JOIN hives h2 ON h2.id = ev.hive_id
                     JOIN apiaries a5 ON a5.id = h2.apiary_id
                     WHERE a5.actor_id = act.id AND ev.acknowledged_at IS NULL)
                                                                       AS open_alerts
            FROM actors act
            WHERE act.kind = 'beekeeper'
              AND EXISTS (SELECT 1 FROM apiaries ap WHERE ap.actor_id = act.id
                          AND ap.cluster_id = (SELECT id FROM clusters
                                               WHERE centre_actor_id = %s
                                                  OR coordinator_id = %s LIMIT 1))
            ORDER BY declared_kg DESC
        """, (actor_id, actor_id))
        members = [dict(r) for r in cur.fetchall()]

        # lots currently held by this FPO
        cur.execute("""
            SELECT b.batch_code, b.kind, b.current_kg, b.floral_source, b.status,
                   b.harvest_date, ap.name AS apiary, act.name AS producer
            FROM batches b
            LEFT JOIN apiaries ap ON ap.id = b.apiary_id
            LEFT JOIN actors act  ON act.id = ap.actor_id
            WHERE b.id IN (
                SELECT ce.batch_id FROM custody_events ce
                WHERE ce.to_actor_id = %s
                  AND ce.ts = (SELECT max(ts) FROM custody_events c2
                               WHERE c2.batch_id = ce.batch_id))
            ORDER BY b.created_at DESC LIMIT 40
        """, (actor_id,))
        holding = [dict(r) for r in cur.fetchall()]

    total_ceiling = sum(float(m["ceiling_kg"] or 0) for m in members)
    total_declared = sum(float(m["declared_kg"] or 0) for m in members)
    return {
        "fpo": me,
        "summary": {
            "members": len(members),
            "hives": sum(m["hives"] for m in members),
            "sentinels": sum(m["sentinels"] for m in members),
            "season_ceiling_kg": round(total_ceiling, 1),
            "declared_kg": round(total_declared, 1),
            "headroom_kg": round(max(0.0, total_ceiling - total_declared), 1),
            "open_alerts": sum(m["open_alerts"] for m in members),
            "lots_held": len(holding),
            "kg_held": round(sum(float(l["current_kg"] or 0) for l in holding), 1),
        },
        "members": members,
        "holding": holding,
    }


# --------------------------------------------------------------------------
# processor
# --------------------------------------------------------------------------
def processor_console(conn, chain, actor_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id::text, name, kind, meta FROM actors WHERE id=%s",
                    (actor_id,))
        me = cur.fetchone()
        if not me:
            raise ValueError(f"unknown actor {actor_id}")

        cur.execute("""
            SELECT b.batch_code, b.kind, b.current_kg, b.status, b.moisture_pct,
                   b.floral_source, b.gtin, b.jar_gtin,
                   EXISTS (SELECT 1 FROM attestations a
                           WHERE a.subject_id = b.id AND a.kind='lab_report'
                             AND (a.summary->>'verdict') = 'pass') AS lab_passed,
                   EXISTS (SELECT 1 FROM seals s WHERE s.batch_id = b.id) AS sealed,
                   (SELECT count(*) FROM seals s WHERE s.batch_id = b.id) AS jar_count
            FROM batches b
            WHERE b.owner_actor_id = %s AND b.status NOT IN ('void')
            ORDER BY b.created_at DESC LIMIT 40
        """, (actor_id,))
        batches = [dict(r) for r in cur.fetchall()]

    # What is blocked, and on what. This is the processor's actual worklist:
    # a batch that cannot be sealed is money sitting still.
    for b in batches:
        if b["sealed"]:
            b["next_action"] = "packed"
        elif not b["lab_passed"]:
            b["next_action"] = "awaiting lab"
            b["blocked_by"] = "No passing NABL report. Seals cannot be issued."
        else:
            b["next_action"] = "ready to pack"
        if chain:
            oc = chain.batch(b["batch_code"])
            if oc:
                b["on_chain_kg"] = oc["minted_kg"]

    return {
        "processor": me,
        "summary": {
            "batches": len(batches),
            "kg_in_custody": round(sum(float(b["current_kg"] or 0) for b in batches), 1),
            "awaiting_lab": sum(1 for b in batches if b["next_action"] == "awaiting lab"),
            "ready_to_pack": sum(1 for b in batches if b["next_action"] == "ready to pack"),
            "packed": sum(1 for b in batches if b["next_action"] == "packed"),
            "jars_issued": sum(b["jar_count"] for b in batches),
        },
        "batches": batches,
    }


# --------------------------------------------------------------------------
# KVIC / regulator
# --------------------------------------------------------------------------
def admin_console(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.code, c.name, c.district, c.state,
                   count(DISTINCT ap.id)                              AS apiaries,
                   count(DISTINCT h.id)                               AS hives,
                   count(DISTINCT h.id) FILTER (WHERE h.is_sentinel)  AS sentinels,
                   count(DISTINCT ap.actor_id)                        AS beekeepers
            FROM clusters c
            LEFT JOIN apiaries ap ON ap.cluster_id = c.id
            LEFT JOIN hives h     ON h.apiary_id = ap.id
            GROUP BY c.id, c.code, c.name, c.district, c.state
            ORDER BY c.code
        """)
        clusters = [dict(r) for r in cur.fetchall()]

        # THE question a ministry asks: are the boxes we gave out producing?
        cur.execute("""
            -- No join to yield_envelopes here: an apiary has many envelopes
            -- over time, and joining them multiplies every hive row. That fan-out
            -- reported 290 hives out of an actual 50 and a "share" of 3.86.
            SELECT h.source,
                   count(*)                              AS hives,
                   count(*) FILTER (WHERE h.is_sentinel) AS sentinels,
                   count(DISTINCT h.apiary_id)           AS apiaries
            FROM hives h
            WHERE h.status = 'active'
            GROUP BY h.source ORDER BY hives DESC
        """)
        attribution = [dict(r) for r in cur.fetchall()]

        cur.execute("""SELECT kind, count(*) AS n, round(avg(score)::numeric,3) AS avg_score
                       FROM fraud_cases WHERE status='open'
                       GROUP BY kind ORDER BY n DESC""")
        fraud = [dict(r) for r in cur.fetchall()]

        cur.execute("""SELECT event_type, severity, count(*) AS n
                       FROM hive_events WHERE acknowledged_at IS NULL
                       GROUP BY event_type, severity ORDER BY n DESC""")
        alerts = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT act.name AS lab, count(*) AS reports,
                   count(*) FILTER (WHERE (a.summary->>'verdict')='pass') AS passed
            FROM attestations a
            LEFT JOIN actors act ON act.id = a.issuer_actor_id
            WHERE a.kind = 'lab_report' GROUP BY act.name
        """)
        labs = [dict(r) for r in cur.fetchall()]

        cur.execute("""SELECT count(*) AS jars,
                              count(*) FILTER (WHERE scan_count > 0) AS scanned,
                              coalesce(sum(scan_count),0) AS total_scans
                       FROM seals""")
        seals = dict(cur.fetchone())

    total_hives = sum(c["hives"] for c in clusters)
    kvic_hives = sum(a["hives"] for a in attribution if a["source"] == "kvic_distributed")
    return {
        "clusters": clusters,
        "scheme_attribution": {
            "by_source": attribution,
            "kvic_distributed_hives": kvic_hives,
            "share_of_monitored_hives": (
                round(kvic_hives / total_hives, 4) if total_hives else None),
            "note": ("Which distributed bee boxes are actually producing is the "
                     "question a scheme is judged on, and until now nothing "
                     "could answer it per box."),
        },
        "fraud_cases_open": fraud,
        "hive_alerts_open": alerts,
        "labs": labs,
        "seals": seals,
        "totals": {
            "clusters": len(clusters),
            "hives": total_hives,
            "sentinels": sum(c["sentinels"] for c in clusters),
            "beekeepers": sum(c["beekeepers"] for c in clusters),
        },
    }

-- Reference data seeded on first boot.
-- Flow windows and peak gains are typical field values for Indian conditions;
-- they are starting priors for the simulator, not gospel. Once a cluster is
-- live, per-district curves get fitted from real hive-weight data and these
-- rows become the cold-start fallback only.
INSERT INTO flora_calendar (flora, state, start_month, end_month, peak_kg_per_day, notes) VALUES
  ('mustard',    'Uttar Pradesh', 11, 2, 2.6, 'Major north Indian flow; migratory apiaries converge here'),
  ('mustard',    'Rajasthan',     11, 2, 2.4, NULL),
  ('mustard',    'Punjab',        11, 2, 2.5, NULL),
  ('mustard',    'Madhya Pradesh',11, 1, 2.2, NULL),
  ('litchi',     'Bihar',          3, 4, 3.1, 'Short, intense flow; premium monofloral honey'),
  ('litchi',     'Uttar Pradesh',  3, 4, 2.8, NULL),
  ('eucalyptus', 'Punjab',         2, 4, 1.8, 'Also a secondary Oct-Dec flow in some belts'),
  ('eucalyptus', 'Haryana',        2, 4, 1.7, NULL),
  ('sunflower',  'Karnataka',      2, 4, 2.0, NULL),
  ('sunflower',  'Maharashtra',    1, 3, 1.9, NULL),
  ('coriander',  'Rajasthan',      2, 3, 1.5, NULL),
  ('berseem',    'Haryana',        2, 4, 1.4, NULL),
  ('acacia',     'Uttar Pradesh',  2, 4, 1.6, 'Babool; drought-tolerant belt flow'),
  ('jamun',      'Maharashtra',    3, 4, 1.3, NULL),
  ('neem',       'Tamil Nadu',     3, 5, 1.2, NULL),
  ('rubber',     'Kerala',        12, 3, 2.1, 'Extrafloral nectaries'),
  ('apple',      'Himachal Pradesh',4,5, 1.5, 'Pollination-led; honey is secondary income'),
  ('multiflora', NULL,             9,11, 1.0, 'Post-monsoon wild flow; the default when nothing else applies'),
  ('dearth',     NULL,             6, 8, 0.0, 'Monsoon dearth: colonies consume stores, weight falls');

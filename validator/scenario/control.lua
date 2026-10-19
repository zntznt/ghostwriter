-- Ghostwriter validator harness. Loaded as a scenario by validator/server.py;
-- Python calls it over RCON:
--   /sc remote.call("ghostwriter", "validate", "<blueprint string>")
-- and gets back JSON via rcon.print:
--   {import = rc, export = "<string>", builds = {[path] = build}}
-- rc is LuaItemStack.import_stack's result (0 ok, -1 ok with errors, 1 fail).
-- Each build covers one blueprint (path "" or "1/0" inside books):
--   {entities = n, unplaced = {...}, unplaceable = {...}, unrevived = {...},
--    rebuilt = "<string>"}
-- unplaceable: names that can't be placed even alone on the prepared test
-- site; unplaced: the rest, i.e. the blueprint itself collides; unrevived:
-- placed as ghosts but not buildable (e.g. elevated rails without supports).
-- The site is prepared per blueprint: a surface meeting its entities'
-- surface_conditions (or a real planet surface when only that works), a base
-- tile, the tiles each entity's tile_buildability_rules need, and a resource
-- under each mining drill.

local SURFACE = "ghostwriter"
local MARGIN = 8
local BASE_TILES = {"refined-concrete", "space-platform-foundation"}

local function new_surface(name)
  return game.create_surface(name, {default_enable_all_autoplace_controls = false, no_enemies_mode = true})
end

-- The default test surface (planet-like default properties).
local function surface()
  return game.surfaces[SURFACE] or new_surface(SURFACE)
end

local function clear(s, area)
  for _, e in pairs(s.find_entities_filtered{area = area}) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
end

local function prepare(s, area, tile)
  local lt, rb = area[1], area[2]
  local centre = {(lt[1] + rb[1]) / 2, (lt[2] + rb[2]) / 2}
  s.request_to_generate_chunks(centre, math.ceil(math.max(rb[1] - lt[1], rb[2] - lt[2]) / 64) + 1)
  s.force_generate_chunk_requests()
  clear(s, area)
  local tiles = {}
  for x = math.floor(lt[1]), math.ceil(rb[1]) do
    for y = math.floor(lt[2]), math.ceil(rb[2]) do
      tiles[#tiles + 1] = {name = tile or "refined-concrete", position = {x, y}}
    end
  end
  s.set_tiles(tiles)
end

local function bounds(entities, tiles)
  local x1, y1, x2, y2 = 0, 0, 0, 0
  for _, list in pairs({entities, tiles}) do
    for _, e in pairs(list) do
      x1, y1 = math.min(x1, e.position.x), math.min(y1, e.position.y)
      x2, y2 = math.max(x2, e.position.x), math.max(y2, e.position.y)
    end
  end
  local r = math.max(-x1, -y1, x2, y2) + MARGIN
  return {{-r, -r}, {r, r}}
end

local function key(name, x, y)
  return name .. "@" .. (math.floor(x * 8 + 0.5) / 8) .. "," .. (math.floor(y * 8 + 0.5) / 8)
end

-- A surface whose properties satisfy every entity's surface_conditions:
-- each property at its default, clamped into the allowed range. Surfaces are
-- created on demand and named after the properties that differ.
local function surface_for(entities)
  local range = {}
  for _, e in pairs(entities) do
    local proto = prototypes.entity[e.name]
    for _, c in pairs((proto and proto.surface_conditions) or {}) do
      local r = range[c.property] or {-math.huge, math.huge}
      range[c.property] = {math.max(r[1], c.min or -math.huge), math.min(r[2], c.max or math.huge)}
    end
  end
  local changed = {}
  for name, sp in pairs(prototypes.surface_property) do
    local r = range[name]
    if r then
      local v = math.min(math.max(sp.default_value, r[1]), r[2])
      if v ~= sp.default_value then changed[#changed + 1] = {name, v} end
    end
  end
  if #changed == 0 then return surface() end
  table.sort(changed, function(a, b) return a[1] < b[1] end)
  local parts = {}
  for _, c in pairs(changed) do parts[#parts + 1] = c[1] .. "_" .. c[2] end
  local name = SURFACE .. "-" .. table.concat(parts, "-")
  local s = game.surfaces[name]
  if not s then
    s = new_surface(name)
    for _, c in pairs(changed) do s.set_property(c[1], c[2]) end
  end
  return s
end

local function tile_layers(name)
  local mask = prototypes.tile[name] and prototypes.tile[name].collision_mask
  return (mask and mask.layers) or {}
end

-- First tile (common ones first) with every required layer and no colliding one.
local function tile_for(required, colliding)
  local function fits(name)
    local layers = tile_layers(name)
    for layer in pairs(required or {}) do if not layers[layer] then return false end end
    for layer in pairs(colliding or {}) do if layers[layer] then return false end end
    return true
  end
  for _, name in pairs({"refined-concrete", "space-platform-foundation", "water", "deepwater", "empty-space"}) do
    if prototypes.tile[name] and fits(name) then return name end
  end
  local names = {}
  for name in pairs(prototypes.tile) do names[#names + 1] = name end
  table.sort(names)
  for _, name in pairs(names) do if fits(name) then return name end end
end

-- Rotate a north-facing offset clockwise to `direction` (16-way; cardinals only).
local function rotate(x, y, direction)
  local quarter = math.floor(((direction or 0) % 16) / 4)
  for _ = 1, quarter do x, y = -y, x end
  return x, y
end

-- Tiles an entity's tile_buildability_rules demand (water under an offshore
-- pump, empty space behind a thruster), set at its blueprint position.
local function apply_tile_rules(s, e, base)
  local proto = prototypes.entity[e.name]
  local tiles = {}
  for _, rule in pairs((proto and proto.tile_buildability_rules) or {}) do
    local tile = tile_for(rule.required_tiles and rule.required_tiles.layers,
                          rule.colliding_tiles and rule.colliding_tiles.layers)
    if tile and tile ~= base then
      local a, b = rule.area.left_top, rule.area.right_bottom
      local ax, ay = rotate(a.x, a.y, e.direction)
      local bx, by = rotate(b.x, b.y, e.direction)
      local x1, x2 = math.min(ax, bx) + e.position.x, math.max(ax, bx) + e.position.x
      local y1, y2 = math.min(ay, by) + e.position.y, math.max(ay, by) + e.position.y
      -- Every tile the area overlaps (its edges can fall on tile centres).
      local eps = 1 / 256
      for x = math.floor(x1 + eps), math.ceil(x2 - eps) - 1 do
        for y = math.floor(y1 + eps), math.ceil(y2 - eps) - 1 do
          tiles[#tiles + 1] = {name = tile, position = {x, y}}
        end
      end
    end
  end
  if #tiles > 0 then s.set_tiles(tiles) end
end

-- A resource under a mining drill or pumpjack, of a category it can mine.
local function apply_resources(s, e)
  local proto = prototypes.entity[e.name]
  if not (proto and proto.type == "mining-drill") then return end
  local categories = proto.resource_categories or {}
  local names = {}
  for name, p in pairs(prototypes.entity) do
    if p.type == "resource" and categories[p.resource_category] then names[#names + 1] = name end
  end
  table.sort(names)
  local resource = names[1]
  if not resource then return end
  local box = proto.collision_box
  local spots = {{e.position.x, e.position.y}}
  for x = math.floor(e.position.x + box.left_top.x), math.ceil(e.position.x + box.right_bottom.x) - 1 do
    for y = math.floor(e.position.y + box.left_top.y), math.ceil(e.position.y + box.right_bottom.y) - 1 do
      spots[#spots + 1] = {x + 0.5, y + 0.5}
    end
  end
  for _, at in pairs(spots) do
    pcall(s.create_entity, {name = resource, position = at, amount = 100000})
  end
end

-- Entity names whose placement doesn't depend on tiles or resources the
-- harness prepares per entity (those can't be judged standing alone).
local function plain_names(entities)
  local names, n = {}, 0
  for _, e in pairs(entities) do
    local proto = prototypes.entity[e.name]
    local special = proto and (proto.type == "mining-drill" or next(proto.tile_buildability_rules or {}))
    if proto and not special and not names[e.name] then names[e.name] = true n = n + 1 end
  end
  return names, n
end

-- How many of `names` stand alone on surface s with base tile `tile`.
local function fits(s, names, area, tile)
  local probe = {area[2][1] + 2 * MARGIN, 0}
  prepare(s, {{probe[1] - MARGIN, -MARGIN}, {probe[1] + MARGIN, MARGIN}}, tile)
  local n = 0
  for name in pairs(names) do
    if s.can_place_entity{name = name, position = probe, force = "player",
                          build_check_type = defines.build_check_type.manual} then n = n + 1 end
  end
  return n
end

-- Where to build: the surface matching the entities' surface_conditions,
-- else a real planet surface (some restrictions, like Fulgora's lightning rods,
-- only show on the planet itself), with the base tile that fits most entities.
local function site_for(entities, area)
  local names, total = plain_names(entities)
  local best = {score = -1}
  local function try(s)
    for _, tile in pairs(BASE_TILES) do
      if prototypes.tile[tile] then
        local n = fits(s, names, area, tile)
        if n > best.score then best = {surface = s, tile = tile, score = n} end
        if n == total then return true end
      end
    end
  end
  if try(surface_for(entities)) then return best.surface, best.tile end
  local planets = {}
  for name in pairs(game.planets) do planets[#planets + 1] = name end
  table.sort(planets)
  for _, name in pairs(planets) do
    local planet = game.planets[name]
    local s = planet.surface or planet.create_surface()
    s.peaceful_mode = true
    if try(s) then break end
  end
  return best.surface, best.tile
end

-- Build a blueprint where its entities stand in the blueprint (absolute
-- snapping on a 2-tile grid keeps every position, rails included), on a surface
-- and tiles that meet its entities' needs, then revive it. Several passes, so
-- entities that need others built first (rolling stock on rails) get them.
-- Returns the report part shared by validation and circuit tests, the surface,
-- the area, and the built entities keyed by blueprint entity_number.
local function place(stack)
  local entities = stack.get_blueprint_entities() or {}
  local area = bounds(entities, stack.get_blueprint_tiles() or {})
  local s, tile = site_for(entities, area)
  prepare(s, area, tile)
  for _, e in pairs(entities) do
    apply_tile_rules(s, e, tile)
    apply_resources(s, e)
  end
  stack.blueprint_snap_to_grid = {2, 2}
  stack.blueprint_absolute_snapping = true
  stack.blueprint_position_relative_to_grid = {0, 0}

  local number_at, wanted = {}, {}
  for _, e in pairs(entities) do
    number_at[key(e.name, e.position.x, e.position.y)] = e.entity_number
    wanted[e.name] = (wanted[e.name] or 0) + 1
  end
  local out = {entities = #entities, surface = s.name, tile = tile, unplaced = {}, unplaceable = {}, unrevived = {}}
  local built, count = {}, {}
  for _ = 1, 4 do
    local ghosts = stack.build_blueprint{
      surface = s, force = "player", position = {0, 0},
      build_mode = defines.build_mode.forced, skip_fog_of_war = false,
    }
    local progress = false
    -- Tile ghosts first so entities that need the tiles can revive.
    for _, g in pairs(ghosts) do
      if g.valid and g.name == "tile-ghost" then g.revive() end
    end
    for _, g in pairs(ghosts) do
      if g.valid and g.name == "entity-ghost" then
        local number = number_at[key(g.ghost_name, g.position.x, g.position.y)]
        local name = g.ghost_name
        local _, entity = g.revive()
        if entity then
          progress = true
          count[name] = (count[name] or 0) + 1
          if number then built[number] = entity end
        end
      end
    end
    if not progress then break end
    -- Re-place what didn't build: a ghost laid before its rails existed
    -- (rolling stock) only builds as a fresh ghost on the real rails.
    for _, g in pairs(s.find_entities_filtered{area = area, name = "entity-ghost"}) do g.destroy() end
  end

  local ghosts_left = {}
  for _, g in pairs(s.find_entities_filtered{area = area, name = "entity-ghost"}) do
    out.unrevived[#out.unrevived + 1] = g.ghost_name
    ghosts_left[g.ghost_name] = (ghosts_left[g.ghost_name] or 0) + 1
  end
  local probe = {area[2][1] + 2 * MARGIN, 0}
  for name, n in pairs(wanted) do
    -- Placed as a ghost but not built is reported as unrevived, not here.
    local missing = n - (count[name] or 0) - (ghosts_left[name] or 0)
    if missing > 0 then
      prepare(s, {{probe[1] - MARGIN, -MARGIN}, {probe[1] + MARGIN, MARGIN}}, tile)
      local alone = s.can_place_entity{name = name, position = probe, force = "player",
                                       build_check_type = defines.build_check_type.manual}
      -- Placeable alone but not here: the blueprint itself collides. Otherwise
      -- the test surface can't hold it (needs something not prepared).
      local list = alone and out.unplaced or out.unplaceable
      list[#list + 1] = name .. " x" .. missing
    end
  end
  return out, s, area, built
end

local function build(stack)
  local out, s, area = place(stack)
  local inv = game.create_inventory(1)
  local copy = inv[1]
  copy.set_stack("blueprint")
  copy.create_blueprint{
    surface = s, force = "player", area = area,
    include_entities = true, include_modules = true, include_station_names = true,
    include_trains = true, include_fuel = true,
  }
  if copy.is_blueprint_setup() then out.rebuilt = copy.export_stack() end
  inv.destroy()
  clear(s, area)
  return out
end

local function build_all(stack, path, builds)
  if stack.is_blueprint and stack.is_blueprint_setup() then
    builds[path] = build(stack)
  elseif stack.is_blueprint_book then
    local inv = stack.get_inventory(defines.inventory.item_main)
    for i = 1, #inv do
      if inv[i].valid_for_read then
        -- Children are addressed by their 0-based book index, as in the JSON.
        build_all(inv[i], (path == "" and "" or path .. "/") .. (i - 1), builds)
      end
    end
  end
end

-- Circuit tests: build one blueprint and keep it, powered by a global
-- electric network; inject signals with script-wired constant combinators
-- (no reach limit); read networks and logistic requests after some ticks.

local function powered(s)
  s.create_global_electric_network()
  storage.power = storage.power or {}
  local source = storage.power[s.name]
  if not (source and source.valid) then
    local at = {-1000, -1000}
    s.request_to_generate_chunks(at, 1)
    s.force_generate_chunk_requests()
    storage.power[s.name] = s.create_entity{name = "electric-energy-interface", position = at, force = "player"}
  end
end

local function signal_id(name)
  for _, kind in pairs({"item", "fluid", "virtual_signal"}) do
    if prototypes[kind][name] then
      return {type = (kind == "virtual_signal") and "virtual" or kind, name = name, quality = "normal"}
    end
  end
end

local function signals_of(network)
  local out = {}
  for _, sig in pairs((network and network.signals) or {}) do
    local name = sig.signal.name
    if sig.signal.quality and sig.signal.quality ~= "normal" then name = name .. "@" .. sig.signal.quality end
    out[name] = sig.count
  end
  return out
end

local function circuit_teardown()
  local c = storage.circuit
  if not c then return end
  for _, inj in pairs(c.injectors) do
    if inj.valid then inj.destroy() end
  end
  clear(c.surface, c.area)
  storage.circuit = nil
end

-- Flow tests: build one blueprint and keep it, powered only through its own
-- poles (a script-wired pole joins them to an unlimited source); every tick
-- top up fed belt lanes from their back end and empty drain belts, counting
-- what left them.

local LANES = {left = defines.transport_line.left_line, right = defines.transport_line.right_line}

local function flow_tick()
  local f = storage.flow
  if not (f and f.started) then return end
  for _, feed in pairs(f.feeds) do
    if feed.belt.valid then
      local line = feed.belt.get_transport_line(feed.lane)
      if line.can_insert_at_back() then line.insert_at_back({name = feed.item}) end
    end
  end
  for _, belt in pairs(f.drains) do
    if belt.valid then
      for _, lane in pairs(LANES) do
        local line = belt.get_transport_line(lane)
        for _, item in pairs(line.get_contents()) do
          f.counts[item.name] = (f.counts[item.name] or 0) + item.count
        end
        line.clear()
      end
    end
  end
end
script.on_event(defines.events.on_tick, flow_tick)

local function source_for(s, area, pole)
  local spot = s.find_non_colliding_position("medium-electric-pole", {area[2][1] + 2, area[1][2] + 2}, 64, 1)
  local helper = s.create_entity{name = "medium-electric-pole", position = spot, force = "player"}
  local eei = s.create_entity{
    name = "electric-energy-interface", force = "player",
    position = s.find_non_colliding_position("electric-energy-interface", spot, 3, 0.5),
  }
  eei.power_production = 1e9
  eei.electric_buffer_size = 1e9
  local copper = defines.wire_connector_id.pole_copper
  helper.get_wire_connector(copper, true).connect_to(pole.get_wire_connector(copper, true), false,
                                                      defines.wire_origin.script)
  return {helper, eei}
end

local function flow_teardown()
  local f = storage.flow
  if not f then return end
  game.speed = 1
  local recipes = game.forces.player.recipes
  for _, name in pairs(f.locked) do recipes[name].enabled = false end
  for _, e in pairs(f.helpers) do
    if e.valid then e.destroy() end
  end
  clear(f.surface, f.area)
  storage.flow = nil
end

local function reply(t)
  rcon.print(helpers.table_to_json(t))
end

remote.add_interface("ghostwriter", {
  circuit_setup = function(text)
    circuit_teardown()
    flow_teardown()
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local rc = stack.import_stack(text)
    if not (stack.valid_for_read and stack.is_blueprint) then
      inv.destroy()
      return reply{error = "not a blueprint (import_stack returned " .. rc .. ")"}
    end
    local out, s, area, built = place(stack)
    powered(s)
    inv.destroy()
    storage.circuit = {surface = s, area = area, built = built, injectors = {}}
    local numbers = {}
    for n in pairs(built) do numbers[#numbers + 1] = n end
    out.built = numbers
    reply(out)
  end,

  circuit_inject = function(json)
    local args = helpers.json_to_table(json)
    local c = storage.circuit
    local target = c and c.built[args.entity]
    if not (target and target.valid) then return reply{error = "entity " .. args.entity .. " was not built"} end
    local connector_id = defines.wire_connector_id[args.connector]
    local target_connector = connector_id and target.get_wire_connector(connector_id, true)
    if not target_connector then return reply{error = target.name .. " has no connector " .. args.connector} end

    local slot = args.entity .. ":" .. args.connector
    local inj = c.injectors[slot]
    if not (inj and inj.valid) then
      local s = c.surface
      local spot = s.find_non_colliding_position("constant-combinator", {c.area[2][1] + 2, c.area[1][2] + 2}, 64, 1)
      inj = s.create_entity{name = "constant-combinator", position = spot, force = "player"}
      local color = args.connector:find("green") and defines.wire_connector_id.circuit_green
                    or defines.wire_connector_id.circuit_red
      inj.get_wire_connector(color, true).connect_to(target_connector, false, defines.wire_origin.script)
      c.injectors[slot] = inj
    end
    local section = inj.get_control_behavior().get_section(1)
    for i = section.filters_count, 1, -1 do section.clear_slot(i) end
    local i = 0
    for name, count in pairs(args.signals or {}) do
      local id = signal_id(name)
      if not id then return reply{error = "unknown signal " .. name} end
      i = i + 1
      section.set_slot(i, {value = id, min = count})
    end
    reply{ok = true}
  end,

  circuit_read = function(json)
    local args = helpers.json_to_table(json)
    local c = storage.circuit
    local entity = c and c.built[args.entity]
    if not (entity and entity.valid) then return reply{error = "entity " .. args.entity .. " was not built"} end
    local out = {}
    if args.connector then
      local id = defines.wire_connector_id[args.connector]
      if not id then return reply{error = "no connector " .. args.connector} end
      out.signals = signals_of(entity.get_circuit_network(id))
    end
    local point = entity.type == "logistic-container"
      and entity.get_logistic_point(defines.logistic_member_index.logistic_container)
    if point then
      out.requests = {}
      for _, f in pairs(point.filters or {}) do
        if f.name and f.count ~= 0 then out.requests[f.name] = f.count end
      end
    end
    for k, v in pairs(defines.entity_status) do
      if v == entity.status then out.status = k end
    end
    reply(out)
  end,

  circuit_teardown = function()
    circuit_teardown()
    reply{ok = true}
  end,

  flow_setup = function(json)
    circuit_teardown()
    flow_teardown()
    local args = helpers.json_to_table(json)
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local rc = stack.import_stack(args.blueprint)
    if not (stack.valid_for_read and stack.is_blueprint) then
      inv.destroy()
      return reply{error = "not a blueprint (import_stack returned " .. rc .. ")"}
    end
    local out, s, area, built = place(stack)
    inv.destroy()
    -- Circuit tests leave the surface on a global network, which would power
    -- every entity whatever the poles do.
    if s.has_global_electric_network then s.destroy_global_electric_network() end
    local f = {surface = s, area = area, built = built, helpers = {}, feeds = {}, drains = {}, counts = {},
               locked = {}}
    for _, e in pairs(built) do
      if e.valid and e.type == "electric-pole" then
        f.helpers = source_for(s, area, e)
        break
      end
    end
    storage.flow = f
    game.speed = args.speed or 1
    -- Furnaces only take what an unlocked recipe smelts (iron plates need
    -- Steel processing); flow tests measure throughput, not research.
    -- (LuaForce.reset_recipes doesn't relock them, so teardown does.)
    for name, recipe in pairs(game.forces.player.recipes) do
      if not recipe.enabled then
        f.locked[#f.locked + 1] = name
        recipe.enabled = true
      end
    end
    reply(out)
  end,

  flow_start = function(json)
    local args = helpers.json_to_table(json)
    local f = storage.flow
    if not f then return reply{error = "no flow_setup"} end
    for _, feed in pairs(args.feeds) do
      local belt = f.built[feed.entity]
      if not (belt and belt.valid and belt.get_max_transport_line_index() >= 2) then
        return reply{error = "entity " .. feed.entity .. " is not a built belt"}
      end
      if not LANES[feed.lane] then return reply{error = "lane is left or right, not " .. feed.lane} end
      if not prototypes.item[feed.item] then return reply{error = "unknown item " .. feed.item} end
      f.feeds[#f.feeds + 1] = {belt = belt, lane = LANES[feed.lane], item = feed.item}
    end
    for _, n in pairs(args.drains) do
      local belt = f.built[n]
      if not (belt and belt.valid and belt.get_max_transport_line_index() >= 2) then
        return reply{error = "entity " .. n .. " is not a built belt"}
      end
      f.drains[#f.drains + 1] = belt
    end
    for _, feed in pairs(args.fluids or {}) do
      local target = f.built[feed.entity]
      if not (target and target.valid) then return reply{error = "entity " .. feed.entity .. " was not built"} end
      if not prototypes.fluid[feed.fluid] then return reply{error = "unknown fluid " .. feed.fluid} end
      -- An unlimited source on the first free tile beside it; it connects like a pipe.
      local source
      for _, d in pairs({{0, 1}, {0, -1}, {1, 0}, {-1, 0}}) do
        local at = {target.position.x + d[1], target.position.y + d[2]}
        if f.surface.can_place_entity{name = "infinity-pipe", position = at, force = "player"} then
          source = f.surface.create_entity{name = "infinity-pipe", position = at, force = "player"}
          break
        end
      end
      if not source then return reply{error = "no free tile beside entity " .. feed.entity} end
      source.set_infinity_pipe_filter{name = feed.fluid, percentage = 1, mode = "at-least"}
      f.helpers[#f.helpers + 1] = source
    end
    f.started = true
    reply{ok = true}
  end,

  flow_count = function()
    local f = storage.flow
    if not f then return reply{error = "no flow_setup"} end
    f.counts, f.since = {}, game.tick
    reply{ok = true}
  end,

  -- Counts since flow_count, and how many of each built entity aren't working.
  flow_read = function()
    local f = storage.flow
    if not (f and f.since) then return reply{error = "no flow_count"} end
    local names = {}
    for k, v in pairs(defines.entity_status) do names[v] = k end
    local stalled = {}
    for _, e in pairs(f.built) do
      local status = e.valid and e.status and names[e.status]
      if status and status ~= "working" then
        local key = e.name .. ": " .. status
        stalled[key] = (stalled[key] or 0) + 1
      end
    end
    reply{counts = f.counts, ticks = game.tick - f.since, stalled = stalled}
  end,

  flow_teardown = function()
    flow_teardown()
    reply{ok = true}
  end,

  -- Fill containers of the built flow blueprint ({entities, item, count}),
  -- or count an item across them ({entities, item}).
  flow_fill = function(json)
    local args = helpers.json_to_table(json)
    local f = storage.flow
    if not f then return reply{error = "no flow_setup"} end
    for _, n in pairs(args.entities) do
      local e = f.built[n]
      if not (e and e.valid) then return reply{error = "entity " .. n .. " was not built"} end
      e.insert{name = args.item, count = args.count}
    end
    reply{ok = true}
  end,

  flow_contents = function(json)
    local args = helpers.json_to_table(json)
    local f = storage.flow
    if not f then return reply{error = "no flow_setup"} end
    local total = 0
    for _, n in pairs(args.entities) do
      local e = f.built[n]
      if not (e and e.valid) then return reply{error = "entity " .. n .. " was not built"} end
      total = total + e.get_item_count(args.item)
    end
    reply{count = total}
  end,

  tick = function()
    rcon.print(game.tick)
  end,

  -- Place every placeable entity, flip its control behaviour settings
  -- (spec: {[LuaXControlBehavior] = {b = {bool attrs}, n = {network attrs}}})
  -- and export each one as its own blueprint.
  harvest = function(spec_json)
    local spec = helpers.json_to_table(spec_json)
    local s = surface()
    local cols, step = 40, 14
    local names = {}
    for name, p in pairs(prototypes.entity) do
      if p.items_to_place_this and not p.hidden then names[#names + 1] = name end
    end
    table.sort(names)
    local rows = math.ceil(#names / cols)
    local area = {{-step, -step}, {cols * step + step, rows * step + step}}
    prepare(s, area)
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local out = {exports = {}, unplaceable = {}}
    for i, name in ipairs(names) do
      local at = {((i - 1) % cols) * step, math.floor((i - 1) / cols) * step}
      local ok, entity = pcall(s.create_entity, {name = name, position = at, force = "player", raise_built = false})
      if ok and entity and entity.valid then
        local cb
        pcall(function() cb = entity.get_or_create_control_behavior() end)
        if cb then
          local flips = spec[cb.object_name] or {b = {}, n = {}}
          for _, attr in pairs(flips.b) do pcall(function() cb[attr] = not cb[attr] end) end
          for _, attr in pairs(flips.n) do pcall(function() cb[attr] = {red = true, green = false} end) end
        end
        local box = entity.bounding_box
        stack.set_stack("blueprint")
        stack.create_blueprint{surface = s, force = "player",
          area = {{box.left_top.x - 0.1, box.left_top.y - 0.1}, {box.right_bottom.x + 0.1, box.right_bottom.y + 0.1}}}
        if stack.is_blueprint_setup() then
          out.exports[#out.exports + 1] = {name = name, type = entity.type, string = stack.export_stack()}
        end
        stack.clear()
      else
        out.unplaceable[#out.unplaceable + 1] = name
      end
    end
    inv.destroy()
    clear(s, area)
    reply(out)
  end,

  validate = function(text)
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local result = {import = stack.import_stack(text), builds = {}}
    if stack.valid_for_read then
      result.export = stack.export_stack()
      build_all(stack, "", result.builds)
    end
    inv.destroy()
    rcon.print(helpers.table_to_json(result))
  end,
  version = function()
    rcon.print(script.active_mods["base"])
  end,
})

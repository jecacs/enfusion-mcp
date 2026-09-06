/**
 * SPDX-License-Identifier: MIT
 * Adapted JsonApiStruct/NetApiHandler patterns from the MIT-licensed upstream:
 * https://github.com/steffenbk/enfusion-mcp-BK
 * Upstream commit: 0acfa884228477043c6b4c2b8c7f0c270d648398
 * See the source repository NOTICE.md for complete attribution.
 *
 * Staged narrow vegetation apply/reconcile endpoint.
 * UNVERIFIED_LIVE until ValidateScripts and the explicit live acceptance flow.
 * APIFunc: EnfusionMCP_VegetationApply
 *
 * The compiled production allowlist is intentionally empty at Checkpoint C,
 * and MUTATION_IMPLEMENTATION_VALIDATED is independently false. Create remains
 * unreachable until the catalog is permissioned and the implementation has
 * compiled and passed the explicit live create/reconcile/Undo validation flow.
 */

class EnfusionMCP_VegetationApplyRequest : JsonApiStruct
{
	string mode;
	string planId;
	string operationId;
	string bridgeBuildId;
	string catalogHash;
	string worldPath;
	int subscene;
	string targetLayer;
	int count;
	float minSpacingM;
	float maxSlopeDeg;
	float scaleMin;
	float scaleMax;
	float terrainYEpsilon;
	ref array<string> prefabs;
	ref array<string> entityNames;
	ref array<float> x;
	ref array<float> y;
	ref array<float> z;
	ref array<float> yaw;
	ref array<float> scale;

	void EnfusionMCP_VegetationApplyRequest()
	{
		// Zero is valid for these fields, so use invalid missing-value sentinels.
		subscene = -1;
		maxSlopeDeg = -1.0;
		RegV("mode");
		RegV("planId");
		RegV("operationId");
		RegV("bridgeBuildId");
		RegV("catalogHash");
		RegV("worldPath");
		RegV("subscene");
		RegV("targetLayer");
		RegV("count");
		RegV("minSpacingM");
		RegV("maxSlopeDeg");
		RegV("scaleMin");
		RegV("scaleMax");
		RegV("terrainYEpsilon");
		prefabs = {};
		entityNames = {};
		x = {};
		y = {};
		z = {};
		yaw = {};
		scale = {};
		RegV("prefabs");
		RegV("entityNames");
		RegV("x");
		RegV("y");
		RegV("z");
		RegV("yaw");
		RegV("scale");
	}
}

class EnfusionMCP_VegetationApplyResponse : JsonApiStruct
{
	string status;
	string errorCode;
	string message;
	string bridgeProtocolVersion;
	string bridgeBuildId;
	string catalogHash;
	string planId;
	string operationId;
	string mode;
	string state;
	int expectedCount;
	int matchingCount;
	int createdCount;
	bool rollbackVerified;
	ref array<string> m_aEntityNames;

	void EnfusionMCP_VegetationApplyResponse()
	{
		RegV("status");
		RegV("errorCode");
		RegV("message");
		RegV("bridgeProtocolVersion");
		RegV("bridgeBuildId");
		RegV("catalogHash");
		RegV("planId");
		RegV("operationId");
		RegV("mode");
		RegV("state");
		RegV("expectedCount");
		RegV("matchingCount");
		RegV("createdCount");
		RegV("rollbackVerified");
		bridgeProtocolVersion = "enfusion-mcp-bridge-v1";
		bridgeBuildId = "enfusion-mcp-bridge-v1-map-agnostic";
		catalogHash = "fe7a5214aebc0cfc171d5b0b218fddeb43c75303a5ae94cc77457c8b7334ee06";
		state = "REJECTED";
		m_aEntityNames = {};
	}

	override void OnPack()
	{
		StartArray("entityNames");
		for (int i = 0; i < m_aEntityNames.Count(); i++)
			ItemString(m_aEntityNames[i]);
		EndArray();
	}
}

class EnfusionMCP_VegetationApply : NetApiHandler
{
	static const string BRIDGE_BUILD_ID = "enfusion-mcp-bridge-v1-map-agnostic";
	static const string CATALOG_HASH = "fe7a5214aebc0cfc171d5b0b218fddeb43c75303a5ae94cc77457c8b7334ee06";
	// This gate must remain false until the staged source compiles and the exact
	// create/reconcile/Undo workflow is explicitly validated in live Workbench.
	static const bool MUTATION_IMPLEMENTATION_VALIDATED = false;
	static const float DEG_TO_RAD = 0.017453292519943295;
	static const float TRANSFORM_EPSILON = 0.01;
	static const float SCALE_EPSILON = 0.000001;

	static void Fail(
		EnfusionMCP_VegetationApplyResponse response,
		string code,
		string text,
		string state = "REJECTED"
	)
	{
		response.status = "error";
		response.errorCode = code;
		response.message = text;
		response.state = state;
	}

	static bool IsFiniteBounded(float value)
	{
		return value == value && Math.AbsFloat(value) <= 1000000.0;
	}

	static bool IsFiniteVector(vector value)
	{
		return IsFiniteBounded(value[0])
			&& IsFiniteBounded(value[1])
			&& IsFiniteBounded(value[2]);
	}

	static bool BoundsAreFiniteAndOrdered(vector boundsMin, vector boundsMax)
	{
		return IsFiniteVector(boundsMin)
			&& IsFiniteVector(boundsMax)
			&& boundsMin[0] <= boundsMax[0]
			&& boundsMin[1] <= boundsMax[1]
			&& boundsMin[2] <= boundsMax[2];
	}

	static float CircularYawDifference(float first, float second)
	{
		float difference = Math.AbsFloat(first - second);
		while (difference >= 360.0)
			difference -= 360.0;
		if (difference > 180.0)
			difference = 360.0 - difference;
		return Math.AbsFloat(difference);
	}

	static bool OrientationMatches(vector yawPitchRoll, float plannedYaw)
	{
		return IsFiniteVector(yawPitchRoll)
			&& CircularYawDifference(yawPitchRoll[0], plannedYaw) <= TRANSFORM_EPSILON
			&& CircularYawDifference(yawPitchRoll[1], 0) <= TRANSFORM_EPSILON
			&& CircularYawDifference(yawPitchRoll[2], 0) <= TRANSFORM_EPSILON;
	}

	static bool IsLowerHex64(string value)
	{
		if (value.Length() != 64)
			return false;
		for (int i = 0; i < value.Length(); i++)
		{
			int code = value.ToAscii(i);
			if (!((code >= 48 && code <= 57) || (code >= 97 && code <= 102)))
				return false;
		}
		return true;
	}

	static bool IsCanonicalUUID(string value)
	{
		if (value.Length() != 36)
			return false;
		for (int i = 0; i < value.Length(); i++)
		{
			if (i == 8 || i == 13 || i == 18 || i == 23)
			{
				if (value.ToAscii(i) != 45)
					return false;
				continue;
			}
			int code = value.ToAscii(i);
			if (!((code >= 48 && code <= 57) || (code >= 97 && code <= 102)))
				return false;
		}
		int variantCode = value.ToAscii(19);
		return variantCode == 56 || variantCode == 57 || variantCode == 97 || variantCode == 98;
	}

	static string ExpectedEntityName(string planId, int index)
	{
		return "EnfusionMCP_" + planId + "_" + index.ToString();
	}

	static bool IsAllowedPrefab(string prefab)
	{
		// Exact production allowlist is intentionally empty at Checkpoint C.
		// Never replace this with Contains(), IndexOf(), a glob, or client input.
		return false;
	}

	static bool CardinalityIsExact(EnfusionMCP_VegetationApplyRequest req)
	{
		return req.prefabs && req.prefabs.Count() == req.count
			&& req.entityNames && req.entityNames.Count() == req.count
			&& req.x && req.x.Count() == req.count
			&& req.y && req.y.Count() == req.count
			&& req.z && req.z.Count() == req.count
			&& req.yaw && req.yaw.Count() == req.count
			&& req.scale && req.scale.Count() == req.count;
	}

	static bool EntityMatches(
		WorldEditorAPI api,
		IEntitySource source,
		EnfusionMCP_VegetationApplyRequest req,
		int index,
		int layerId
	)
	{
		if (!source || source.GetName() != req.entityNames[index])
			return false;
		if (source.GetSubScene() != req.subscene || source.GetLayerID() != layerId)
			return false;

		BaseContainer ancestor = source.GetAncestor();
		if (!ancestor || ancestor.GetResourceName() != req.prefabs[index])
			return false;

		IEntity entity = api.SourceToEntity(source);
		if (!entity)
			return false;
		vector position = entity.GetOrigin();
		vector yawPitchRoll = entity.GetYawPitchRoll();
		float actualScale = entity.GetScale();
		float sourceScale;
		float sourceYaw;
		if (!source.Get("scale", sourceScale)
			|| !source.Get("angleY", sourceYaw) || !IsFiniteBounded(sourceYaw)
			|| !IsFiniteBounded(sourceScale) || sourceScale <= 0 || sourceScale > 10
			|| !IsFiniteVector(position)
			|| !IsFiniteBounded(actualScale) || actualScale <= 0 || actualScale > 10)
			return false;
		if (Math.AbsFloat(position[0] - req.x[index]) > TRANSFORM_EPSILON
			|| Math.AbsFloat(position[1] - req.y[index]) > TRANSFORM_EPSILON
			|| Math.AbsFloat(position[2] - req.z[index]) > TRANSFORM_EPSILON
			|| !OrientationMatches(yawPitchRoll, req.yaw[index])
			|| CircularYawDifference(sourceYaw, req.yaw[index]) > TRANSFORM_EPSILON
			|| Math.AbsFloat(sourceScale - req.scale[index]) > SCALE_EPSILON
			|| Math.AbsFloat(actualScale - req.scale[index]) > SCALE_EPSILON)
			return false;
		return true;
	}

	static int CountEntitiesByName(WorldEditorAPI api, string name, out IEntitySource uniqueEntity)
	{
		uniqueEntity = null;
		int matchingNames = 0;
		int editorEntityCount = api.GetEditorEntityCount();
		for (int entityIndex = 0; entityIndex < editorEntityCount; entityIndex++)
		{
			IEntitySource candidate = api.GetEditorEntity(entityIndex);
			if (!candidate || candidate.GetName() != name)
				continue;
			matchingNames++;
			if (matchingNames == 1)
				uniqueEntity = candidate;
			else
				uniqueEntity = null;
		}
		return matchingNames;
	}

	static bool EntityNamesAreAbsent(WorldEditorAPI api, notnull array<string> createdNames)
	{
		for (int verifyIndex = 0; verifyIndex < createdNames.Count(); verifyIndex++)
		{
			IEntitySource ignoredEntity;
			if (CountEntitiesByName(api, createdNames[verifyIndex], ignoredEntity) != 0)
				return false;
		}
		return true;
	}

	static bool EntitySourcesAreAbsent(
		WorldEditorAPI api,
		notnull array<IEntitySource> created
	)
	{
		int editorEntityCount = api.GetEditorEntityCount();
		for (int entityIndex = 0; entityIndex < editorEntityCount; entityIndex++)
		{
			IEntitySource candidate = api.GetEditorEntity(entityIndex);
			for (int createdIndex = 0; createdIndex < created.Count(); createdIndex++)
			{
				if (candidate && candidate == created[createdIndex])
					return false;
			}
		}
		return true;
	}

	static bool CleanupCreated(WorldEditorAPI api, notnull array<IEntitySource> created)
	{
		bool deleted = true;
		for (int i = created.Count() - 1; i >= 0; i--)
		{
			if (!created[i] || !api.DeleteEntity(created[i]))
				deleted = false;
		}
		return deleted;
	}

	static bool VerifyExactBatch(
		WorldEditorAPI api,
		EnfusionMCP_VegetationApplyRequest req,
		int layerId,
		out int matchingCount
	)
	{
		matchingCount = 0;
		for (int verifyIndex = 0; verifyIndex < req.count; verifyIndex++)
		{
			IEntitySource uniqueEntity;
			if (CountEntitiesByName(api, req.entityNames[verifyIndex], uniqueEntity) != 1)
				return false;
			if (!EntityMatches(api, uniqueEntity, req, verifyIndex, layerId))
				return false;
			matchingCount++;
		}
		return matchingCount == req.count;
	}

	override JsonApiStruct GetRequest()
	{
		return new EnfusionMCP_VegetationApplyRequest();
	}

	override JsonApiStruct GetResponse(JsonApiStruct request)
	{
		EnfusionMCP_VegetationApplyRequest req = EnfusionMCP_VegetationApplyRequest.Cast(request);
		EnfusionMCP_VegetationApplyResponse response = new EnfusionMCP_VegetationApplyResponse();
		if (!req)
		{
			Fail(response, "INVALID_REQUEST", "Request could not be decoded");
			return response;
		}
		response.planId = req.planId;
		response.operationId = req.operationId;
		response.mode = req.mode;
		response.expectedCount = req.count;

		// Validate the complete trusted envelope before obtaining an editor action.
		if (req.mode != "create" && req.mode != "reconcile")
		{
			Fail(response, "INVALID_MODE", "mode must be create or reconcile");
			return response;
		}
		if (!IsLowerHex64(req.planId) || !IsCanonicalUUID(req.operationId))
		{
			Fail(response, "INVALID_OPERATION_ID", "planId or operationId is not canonical");
			return response;
		}
		if (req.bridgeBuildId != BRIDGE_BUILD_ID)
		{
			Fail(response, "BRIDGE_BUILD_MISMATCH", "Python and bridge build IDs differ");
			return response;
		}
		if (req.catalogHash != CATALOG_HASH)
		{
			Fail(response, "CATALOG_MISMATCH", "Python and bridge catalog hashes differ");
			return response;
		}
		if (req.worldPath == "")
		{
			Fail(response, "INVALID_WORLD", "Request must identify the world used for planning");
			return response;
		}
		if (req.subscene < 0)
		{
			Fail(response, "INVALID_SUBSCENE", "subscene is required and must be non-negative");
			return response;
		}
		if (req.targetLayer != "MCP_Preview" && req.targetLayer != "MCP_Vegetation")
		{
			Fail(response, "LAYER_NOT_ALLOWED", "Target layer is not allowlisted");
			return response;
		}
		if (req.count < 1 || req.count > 100 || !CardinalityIsExact(req))
		{
			Fail(response, "INVALID_CARDINALITY", "All placement arrays must exactly match count 1..100");
			return response;
		}
		for (int validationIndex = 0; validationIndex < req.count; validationIndex++)
		{
			if (!IsAllowedPrefab(req.prefabs[validationIndex]))
			{
				Fail(response, "CATALOG_NOT_READY", "Production vegetation allowlist is empty/unverified");
				return response;
			}
			if (req.entityNames[validationIndex] != ExpectedEntityName(req.planId, validationIndex))
			{
				Fail(response, "INVALID_ENTITY_NAME", "Entity name is not derived from planId and index");
				return response;
			}
			if (!IsFiniteBounded(req.x[validationIndex]) || !IsFiniteBounded(req.y[validationIndex])
				|| !IsFiniteBounded(req.z[validationIndex]) || !IsFiniteBounded(req.yaw[validationIndex])
				|| !IsFiniteBounded(req.scale[validationIndex])
				|| req.yaw[validationIndex] < 0 || req.yaw[validationIndex] >= 360)
			{
				Fail(response, "INVALID_TRANSFORM", "Placement transform is invalid or out of range");
				return response;
			}
		}
		WorldEditor worldEditor = Workbench.GetModule(WorldEditor);
		if (!worldEditor)
		{
			Fail(response, "WORLD_EDITOR_NOT_RUNNING", "WorldEditor module is not running");
			return response;
		}
		WorldEditorAPI api = worldEditor.GetApi();
		if (!api)
		{
			Fail(response, "WORLD_EDITOR_API_UNAVAILABLE", "WorldEditorAPI is unavailable");
			return response;
		}

		string actualWorld;
		api.GetWorldPath(actualWorld);
		// Plans are bound to the exact open world and subscene, regardless of map.
		if (actualWorld != req.worldPath || api.GetCurrentSubScene() != req.subscene)
		{
			Fail(response, "STALE_WORLD", "World or subscene changed after planning");
			return response;
		}

		int layerId = api.GetSubsceneLayerId(req.subscene, req.targetLayer);
		if (layerId < 0 || api.GetSubsceneLayerPath(req.subscene, layerId) != req.targetLayer)
		{
			Fail(response, "LAYER_NOT_FOUND", "Exact root-level target layer does not exist");
			return response;
		}

		// A partial view during an edit action or Undo/Redo must never be used to
		// resolve the durable operation or release the target-wide safety fence.
		if (api.IsDoingEditAction() || api.UndoOrRedoIsRestoring())
		{
			Fail(response, "EDITOR_BUSY", "Entity reconciliation requires a stable editor state");
			return response;
		}

		// Reconciliation stops after common identity/world/subscene/layer and
		// stable-editor checks. Layer locks and changed terrain do not hide an
		// otherwise stable deterministic batch from read-only reconciliation.
		int presentCount = 0;
		bool entityConflict = false;
		for (int existingIndex = 0; existingIndex < req.count; existingIndex++)
		{
			IEntitySource existing;
			int nameMultiplicity = CountEntitiesByName(
				api,
				req.entityNames[existingIndex],
				existing
			);
			if (nameMultiplicity == 0)
				continue;
			presentCount += nameMultiplicity;
			if (nameMultiplicity == 1 && EntityMatches(api, existing, req, existingIndex, layerId))
				response.matchingCount++;
			else
				entityConflict = true;
		}
		if (entityConflict)
		{
			Fail(response, "ENTITY_CONFLICT", "A deterministic entity exists with changed data", "CHANGED");
			return response;
		}
		if (presentCount > 0 && response.matchingCount != req.count)
		{
			Fail(response, "PARTIAL_OPERATION", "Only part of the deterministic batch exists", "PARTIAL");
			return response;
		}
		if (response.matchingCount == req.count)
		{
			response.status = "ok";
			response.errorCode = "";
			response.state = "COMPLETE";
			response.message = "Full matching batch already exists";
			return response;
		}
		if (req.mode == "reconcile")
		{
			response.status = "ok";
			response.errorCode = "";
			response.state = "NONE";
			response.message = "No deterministic entities exist; create was not retried";
			return response;
		}

		// Hard compile/live safety gate. Every create request fails before any
		// create-only editor-state, terrain, spacing, or mutation operation.
		if (!MUTATION_IMPLEMENTATION_VALIDATED)
		{
			Fail(
				response,
				"MUTATION_IMPLEMENTATION_UNVALIDATED",
				"Create mutation is disabled until live implementation validation succeeds"
			);
			return response;
		}

		if (!IsFiniteBounded(req.minSpacingM) || req.minSpacingM <= 0
			|| !IsFiniteBounded(req.maxSlopeDeg) || req.maxSlopeDeg < 0 || req.maxSlopeDeg >= 90
			|| !IsFiniteBounded(req.scaleMin) || !IsFiniteBounded(req.scaleMax)
			|| req.scaleMin <= 0 || req.scaleMin > req.scaleMax || req.scaleMax > 10
			|| !IsFiniteBounded(req.terrainYEpsilon) || req.terrainYEpsilon <= 0 || req.terrainYEpsilon > 0.05)
		{
			Fail(response, "INVALID_CONSTRAINT", "Numeric constraints are invalid");
			return response;
		}
		for (int scaleIndex = 0; scaleIndex < req.count; scaleIndex++)
		{
			if (req.scale[scaleIndex] < req.scaleMin || req.scale[scaleIndex] > req.scaleMax)
			{
				Fail(response, "INVALID_SCALE", "Placement scale is outside the declared range");
				return response;
			}
		}

		if (api.IsGameMode())
		{
			Fail(response, "EDIT_MODE_REQUIRED", "WorldEditor must be in normal edit mode");
			return response;
		}
		if (api.IsPrefabEditMode())
		{
			Fail(response, "PREFAB_MODE_FORBIDDEN", "Prefab edit mode is forbidden");
			return response;
		}
		if (api.IsDoingEditAction() || api.UndoOrRedoIsRestoring())
		{
			Fail(response, "EDITOR_BUSY", "Another edit action or Undo/Redo is active");
			return response;
		}
		if (api.IsEntityLayerLockedHierarchy(req.subscene, layerId))
		{
			Fail(response, "LAYER_LOCKED", "Target layer or its parent hierarchy is locked");
			return response;
		}

		vector boundsMin;
		vector boundsMax;
		if (!worldEditor.GetTerrainBounds(boundsMin, boundsMax))
		{
			Fail(response, "TERRAIN_UNAVAILABLE", "Terrain bounds are unavailable");
			return response;
		}
		if (!BoundsAreFiniteAndOrdered(boundsMin, boundsMax))
		{
			Fail(response, "INVALID_TERRAIN_BOUNDS", "Terrain bounds are non-finite or unordered");
			return response;
		}
		BaseWorld world = api.GetWorld();
		if (!world)
		{
			Fail(response, "WORLD_UNAVAILABLE", "WorldEditor world is unavailable");
			return response;
		}
		float minimumNormalY = Math.Cos(req.maxSlopeDeg * DEG_TO_RAD);
		float spacingSquared = req.minSpacingM * req.minSpacingM;
		for (int terrainIndex = 0; terrainIndex < req.count; terrainIndex++)
		{
			if (req.x[terrainIndex] < boundsMin[0] || req.x[terrainIndex] > boundsMax[0]
				|| req.z[terrainIndex] < boundsMin[2] || req.z[terrainIndex] > boundsMax[2])
			{
				Fail(response, "OUTSIDE_TERRAIN_BOUNDS", "Placement is outside terrain bounds");
				return response;
			}
			float currentY;
			if (!api.TryGetTerrainSurfaceY(req.x[terrainIndex], req.z[terrainIndex], currentY)
				|| !IsFiniteBounded(currentY)
				|| Math.AbsFloat(currentY - req.y[terrainIndex]) > req.terrainYEpsilon)
			{
				Fail(response, "STALE_TERRAIN", "Terrain height changed after planning");
				return response;
			}
			vector normalPosition = Vector(req.x[terrainIndex], currentY, req.z[terrainIndex]);
			vector normal = SCR_TerrainHelper.GetTerrainNormal(normalPosition, world);
			float normalLength = Math.Sqrt(
				normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]
			);
			if (!IsFiniteVector(normalPosition)
				|| Math.AbsFloat(normalPosition[1] - currentY) > req.terrainYEpsilon
				|| !IsFiniteVector(normal)
				|| !IsFiniteBounded(normalLength)
				|| normalLength <= 0.000001)
			{
				Fail(response, "STALE_TERRAIN", "Terrain normal/slope changed after planning");
				return response;
			}
			float normalizedY = normal[1] / normalLength;
			if (!IsFiniteBounded(normalizedY) || normalizedY < minimumNormalY)
			{
				Fail(response, "STALE_TERRAIN", "Terrain normal/slope changed after planning");
				return response;
			}

			for (int priorIndex = 0; priorIndex < terrainIndex; priorIndex++)
			{
				float deltaX = req.x[terrainIndex] - req.x[priorIndex];
				float deltaZ = req.z[terrainIndex] - req.z[priorIndex];
				if (deltaX * deltaX + deltaZ * deltaZ < spacingSquared)
				{
					Fail(response, "SPACING_VIOLATION", "Planned placements violate minimum spacing");
					return response;
				}
			}
		}

		if (!api.BeginEntityAction("EnfusionMCP vegetation " + req.planId.Substring(0, 12)))
		{
			Fail(response, "BEGIN_ACTION_FAILED", "BeginEntityAction returned false");
			return response;
		}

		array<IEntitySource> created = {};
		array<string> createdNames = {};
		bool createFailed = false;
		for (int createIndex = 0; createIndex < req.count; createIndex++)
		{
			vector position = Vector(req.x[createIndex], req.y[createIndex], req.z[createIndex]);
			IEntitySource source = api.CreateEntity(
				req.prefabs[createIndex],
				req.entityNames[createIndex],
				layerId,
				null,
				position,
				vector.Zero
			);
			if (!source)
			{
				createFailed = true;
				break;
			}
			// The current entity immediately joins cleanup responsibility.
			created.Insert(source);
			createdNames.Insert(source.GetName());
			response.m_aEntityNames.Insert(req.entityNames[createIndex]);
			response.createdCount++;

			if (source.GetName() != req.entityNames[createIndex])
			{
				if (!api.RenameEntity(source, req.entityNames[createIndex]))
				{
					createFailed = true;
					break;
				}
				createdNames.Insert(source.GetName());
			}
			// Follow the official SampleWorldEditorTool: create at zero rotation,
			// then write the Y-axis source property for yaw. This avoids mixing
			// CreateEntity's rotation argument with IEntity's yaw/pitch/roll API.
			// Both source edits participate in this entity action and Undo; the
			// source is already in the cleanup set if either setter fails.
			float currentSourceYaw;
			float currentSourceScale;
			if (!source.Get("angleY", currentSourceYaw) || !IsFiniteBounded(currentSourceYaw)
				|| !source.Get("scale", currentSourceScale) || !IsFiniteBounded(currentSourceScale))
			{
				createFailed = true;
				break;
			}
			// Avoid asking the editor for a no-change write. The same tolerances
			// are used below when verifying both the source and runtime entity.
			if (CircularYawDifference(currentSourceYaw, req.yaw[createIndex]) > TRANSFORM_EPSILON)
			{
				if (!api.SetVariableValue(source, null, "angleY", req.yaw[createIndex].ToString()))
				{
					createFailed = true;
					break;
				}
			}
			if (currentSourceScale <= 0 || currentSourceScale > 10
				|| Math.AbsFloat(currentSourceScale - req.scale[createIndex]) > SCALE_EPSILON)
			{
				if (!api.SetVariableValue(source, null, "scale", req.scale[createIndex].ToString()))
				{
					createFailed = true;
					break;
				}
			}
			if (!EntityMatches(api, source, req, createIndex, layerId))
			{
				createFailed = true;
				break;
			}
		}

		if (createFailed)
		{
			bool cleanupSucceeded = CleanupCreated(api, created);
			bool actionEnded = api.EndEntityAction("EnfusionMCP vegetation rollback");
			if (!actionEnded)
			{
				response.rollbackVerified = false;
				Fail(response, "UNKNOWN_OUTCOME", "Create failed and the editor action did not end", "UNKNOWN");
				return response;
			}
			bool absentAfterEnd = EntityNamesAreAbsent(api, createdNames);
			bool sourcesAbsentAfterEnd = EntitySourcesAreAbsent(api, created);
			response.rollbackVerified = cleanupSucceeded && absentAfterEnd && sourcesAbsentAfterEnd;
			if (response.rollbackVerified)
				Fail(response, "CREATE_FAILED", "Create failed and rollback was verified", "ROLLBACK_VERIFIED");
			else
				Fail(response, "ROLLBACK_FAILED", "Create failed and full rollback was not proven", "ROLLBACK_FAILED");
			return response;
		}

		bool actionEnded = api.EndEntityAction("EnfusionMCP vegetation " + req.planId.Substring(0, 12));
		if (!actionEnded)
		{
			response.rollbackVerified = false;
			Fail(response, "UNKNOWN_OUTCOME", "End failed; no further mutation was attempted", "UNKNOWN");
			return response;
		}

		int postEndMatchingCount;
		if (!VerifyExactBatch(api, req, layerId, postEndMatchingCount))
		{
			response.matchingCount = postEndMatchingCount;
			response.rollbackVerified = false;
			Fail(
				response,
				"POST_COMMIT_VERIFICATION_FAILED",
				"The completed editor action did not round-trip to one exact deterministic batch",
				"UNKNOWN"
			);
			return response;
		}

		response.status = "ok";
		response.errorCode = "";
		response.state = "COMPLETE";
		response.matchingCount = postEndMatchingCount;
		response.message = "Created one unsaved vegetation batch; user must save manually";
		return response;
	}
}

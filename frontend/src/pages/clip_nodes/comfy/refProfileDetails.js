function formatScalar(value) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) {
    return value
      .map((item) => formatScalar(item))
      .filter(Boolean)
      .join(", ");
  }
  if (typeof value === "object") {
    return Object.values(value)
      .flatMap((item) => (Array.isArray(item) ? item : [item]))
      .map((item) => formatScalar(item))
      .filter(Boolean)
      .join(", ");
  }
  return String(value).trim();
}

export function formatRefProfileDetails(profile) {
  if (!profile || typeof profile !== "object") return [];

  const visual = profile?.visualProfile && typeof profile.visualProfile === "object"
    ? profile.visualProfile
    : {};

  const pick = (...keys) => {
    for (const key of keys) {
      const fromVisual = formatScalar(visual?.[key]);
      if (fromVisual) return fromVisual;
      const fromProfile = formatScalar(profile?.[key]);
      if (fromProfile) return fromProfile;
    }
    return "";
  };

  const type = String(
    pick("type", "kind", "entityType", "category", "objectCategory", "environmentType", "subjectType")
      || profile?.role
      || ""
  ).toLowerCase();

  const pushLine = (label, value, acc) => {
    const normalized = formatScalar(value);
    if (normalized) acc.push(`- ${label}: ${normalized}`);
  };

  const lines = [];
  pushLine("тип", pick("type", "kind", "entityType", "category", "objectCategory", "environmentType", "subjectType"), lines);

  if (type.includes("human") || type.includes("person") || type.includes("character") || type.includes("человек")) {
    pushLine("пол/подача", pick("genderPresentation", "gender", "presentation", "sex"), lines);
    pushLine("возраст", pick("age", "ageRange"), lines);
    pushLine("волосы", pick("hair", "hairStyle"), lines);
    pushLine("одежда", pick("clothing", "outfit", "wardrobe"), lines);
    pushLine("особенности", pick("features", "distinctiveFeatures", "marks"), lines);
  } else if (type.includes("animal") || type.includes("pet") || type.includes("живот")) {
    pushLine("вид", pick("species", "animalType", "breed"), lines);
    pushLine("порода / тип", pick("speciesLock", "breedLikeAppearance", "breed"), lines);
    pushLine("окрас", pick("coat", "furPattern", "color", "coatColor", "furColor"), lines);
    pushLine("морда", pick("muzzleShape", "muzzle"), lines);
    pushLine("уши", pick("earShape", "ears"), lines);
    pushLine("хвост", pick("tailShape", "tail"), lines);
    pushLine("телосложение", pick("bodyBuild", "bodyType", "sizeClass"), lines);
    pushLine("особенности", pick("features", "distinctiveFeatures", "marks", "morphology"), lines);
  } else if (type.includes("location") || type.includes("place") || type.includes("локац") || type.includes("environment")) {
    pushLine("место", pick("place", "location", "scene", "setting", "environmentType"), lines);
    pushLine("поверхность", pick("surface", "ground"), lines);
    pushLine("окружение", pick("environment", "surroundings", "context"), lines);
  } else if (type.includes("style") || type.includes("aesthetic") || type.includes("стил")) {
    pushLine("направление", pick("direction", "styleDirection", "genre"), lines);
    pushLine("свет", pick("lighting", "light"), lines);
    pushLine("атмосфера", pick("mood", "atmosphere"), lines);
  } else {
    pushLine("категория", pick("category", "itemType", "objectType", "objectCategory"), lines);
    pushLine("вид", pick("species"), lines);
    pushLine("цвет", pick("color", "palette"), lines);
    pushLine("материал", pick("material", "materials"), lines);
    pushLine("форма", pick("shape", "form"), lines);
    pushLine("среда", pick("environmentType"), lines);
  }

  if (lines.length) return lines;

  return Object.entries({ ...profile, ...visual })
    .filter(([, value]) => value !== null && value !== undefined && String(formatScalar(value)).trim())
    .map(([key, value]) => `- ${key}: ${formatScalar(value)}`);
}
